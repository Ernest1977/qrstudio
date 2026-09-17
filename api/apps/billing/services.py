"""Ce qui transforme un paiement en droit d'usage — et rien d'autre.

Le contrat, en une phrase : **le client ne demande jamais un plan, il demande un moyen de payer**.
Le plan est accordé par `appliquer_webhook`, sur la foi d'un événement signé dont le `metadata` porte
l'identifiant et le palier *que nous avons choisis*. Toute autre architecture (accord immédiat après un
`302` de retour, ou plan lu dans le corps de la requête) se contourne en une requête curl.

Trois garde-fous méritent leur commentaire parce qu'ils ont déjà coûté de l'argent à quelqu'un :

* **garde monétaire** : on recoupe le montant reçu avec `apps/accounts/plans.py`. Un `price_…` collé
  dans l'environnement qui pointe vers 2,99 € au lieu de 8,99 € ne doit pas offrir Premium au prix
  Standard — c'est une erreur de configuration banale, et sans ce contrôle elle est silencieuse ;
* **idempotence stricte** : Stripe rejoue, et un rejeu de `subscription.updated` qui repousse
  `periode_fin` de trente jours est une remise commerciale non décidée ;
* **sursis sur paiement en retard** : la carte expire un vendredi, la banque répond le lundi. Couper
  dans l'heure, c'est perdre un client pour une raison qui n'est pas la sienne.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts import plans
from apps.billing import mobile as mobile_mod
from apps.billing import stripe_api
from apps.billing.models import Abonnement, EvenementPaiement, Fournisseur, PaiementMobile, Statut

logger = logging.getLogger(__name__)

EVENEMENTS_TRAITES = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.payment_failed",
        "invoice.payment_succeeded",
    }
)


def _base_publique() -> str:
    """L'origine publique du site, celle que le client voit — retours de paiement compris.

    Lu au MEME endroit que `apps/accounts/services.py` (les liens des e-mails) : la valeur vit dans le
    dictionnaire `settings.QR["SHORT_BASE_URL"]`, pas en variable de module. Un
    `getattr(settings, "SHORT_BASE_URL", repli)` retombait donc silencieusement sur le repli, et en
    production le client qui vient de payer etait rejete vers `http://localhost:8000/...` — la machine
    de l'operateur. Deux URLs de retour incohérentes valaient mieux qu'un reglage de plus, mais pas
    au prix de celui-ci.
    """
    # Import local volontaire : ce module lit les reglages a l'appel, jamais a l'import.
    from django.conf import settings

    return str((getattr(settings, "QR", None) or {}).get("SHORT_BASE_URL", "http://localhost:8000")).rstrip("/")


def _epoch(valeur) -> dt.datetime | None:
    """Timestamp Stripe (secondes, UTC) -> datetime aware, ou `None`.

    `make_aware` sur un datetime **déjà** aware lève un `ValueError` — et c'est précisément ce qui
    arrive quand le projet tourne avec `USE_TZ = True` : la conversion doit être faite une fois, ici,
    et pas deux.
    """
    if not valeur:
        return None
    return dt.datetime.fromtimestamp(int(valeur), tz=dt.UTC)


def abonnement_ou_creer(user) -> Abonnement:
    abo, _ = Abonnement.objects.get_or_create(user=user)
    return abo


def accord(
    user,
    *,
    palier: str,
    fournisseur: str,
    statut: str,
    periode_fin=None,
    client_id: str = "",
    abonnement_id: str = "",
    prix_id: str = "",
    en_sursis_jusqu_a=None,
    raison: str = "",
) -> Abonnement:
    """Applique un palier payé au compte, en une seule écriture cohérente.

    `User.plan` et `Abonnement.palier` sont écrits ensemble : le premier est lu par les quotas, le
    second par le portail et l'admin. Les laisser diverger (l'un mis à jour par le webhook, l'autre par
    un script) produit exactement le genre de ticket « le client paie et n'a pas l'accès ».
    """
    with transaction.atomic():
        abo = Abonnement.objects.select_for_update().filter(user=user).first() or Abonnement(user=user)
        abo.fournisseur = fournisseur
        abo.statut = statut
        abo.palier = palier
        abo.periode_fin = periode_fin
        abo.client_id = client_id or abo.client_id
        abo.abonnement_fournisseur_id = abonnement_id or abo.abonnement_fournisseur_id
        abo.prix_id = prix_id or abo.prix_id
        abo.en_sursis_jusqu_a = en_sursis_jusqu_a
        if statut == Statut.ANNULE:
            abo.annule_le = timezone.now()
        abo.save()

        user.plan = palier
        user.plan_until = periode_fin
        # Le gel du quota de QR statiques (migration `0003_gele_quota_statique`) est une **dette
        # contractée auprès d'une cohorte** : « ceux qui avaient 20 les gardent tant qu'ils sont gratuits ».
        # Un compte qui paie n'a plus besoin de cette protection — et un compte qui annule repart de la
        # grille en vigueur, pas de celle d'il y a deux ans. Sans cette remise à zéro, l'aller-retour
        # free(20) → premium → free redonnait 20 places à quelqu'un qui ne les a jamais achetées.
        user.quota_statique_gele = None
        user.quota_statique_palier = ""
        user.save(update_fields=["plan", "plan_until", "quota_statique_gele", "quota_statique_palier"])
    logger.info(
        "facturation palier=%s statut=%s fournisseur=%s user_id=%s raison=%s",
        palier,
        statut,
        fournisseur,
        user.pk,
        raison or "-",
    )
    return abo


def prix_de(palier_code: str) -> dict:
    """Le `price_id` Stripe attendu pour ce palier, avec le montant que la grille annonce.

    Sortir les deux ensemble est le propos : l'appelant peut comparer, et le test aussi.
    """
    from django.conf import settings

    identifiants = getattr(settings, "STRIPE_PRICE_IDS", {}) or {}
    return {
        "price_id": identifiants.get(palier_code, ""),
        "montant_centimes": plans.palier(palier_code).prix_centimes,
        "devise": getattr(settings, "BILLING_DEVISE", "EUR"),
    }


def verifier_montant(palier_code: str, recu_centimes: int | None, devise: str | None = None) -> str | None:
    """`None` si tout concorde, sinon le motif lisible par l'humain qui va réparer la configuration.

    On ne *bloque* pas le paiement (le client a payé, il ne doit pas être puni d'une erreur de
    réglage) : on n'accorde **pas** le palier en catimini, on journalise, et le personnel voit l'événement
    en erreur. Un abonnement accordé au mauvais prix est plus coûteux à défaire qu'à corriger.
    """
    attendu = prix_de(palier_code)
    if recu_centimes is None:
        return None  # l'événement ne porte pas de montant (subscription.updated) : rien à recouper ici
    if int(recu_centimes) != attendu["montant_centimes"]:
        return (
            f"montant recu {recu_centimes} != {attendu['montant_centimes']} centimes attendus pour "
            f"`{palier_code}` (price_id={attendu['price_id'] or 'absent'})"
        )
    if devise and devise.upper() != attendu["devise"].upper():
        return f"devise recue {devise} != {attendu['devise']}"
    return None


# ------------------------------------------------------------------ liens de paiement


def lien_checkout(user, *, palier_code: str) -> dict:
    """Crée une session Stripe Checkout (abonnement) et rend l'URL.

    Trois details qui comptent :

    * le `metadata` est pose **ici** (`user_id`, `palier`) : c'est la seule chose que le webhook pourra
      recouper, et il ne dépend d'aucun champ envoye par le client ;
    * `allow_promotion_codes` est laisse ouvert cote Stripe mais ignore pour la garde monetaire : une
      reduction change le montant, donc la garde compare le `unit_amount` de la ligne, pas le total ;
    * les portefeuilles du telephone (Apple Pay / Google Pay) sont actives sur la meme session : c'est
      le « paiement mobile » qui ne demande aucun contrat nouveau.
    """
    from django.conf import settings

    palier_code = (palier_code or "").strip().lower()
    if palier_code not in plans.PALIERS or palier_code == "free":
        raise stripe_api.ErreurStripe("Ce palier n'est pas facturé.", code="palier_infacturable", statut=400)
    if user.plan_effectif == palier_code:
        raise stripe_api.ErreurStripe("Vous êtes déjà à ce palier.", code="palier_deja_actif", statut=400)
    info = prix_de(palier_code)
    if not info["price_id"]:
        raise stripe_api.ErreurStripe(
            f"Aucun price Stripe n'est configure pour le palier `{palier_code}` (STRIPE_PRICE_{palier_code.upper()}).",
            code="stripe_price_manquant",
            statut=503,
        )

    domaine = _base_publique()
    lignes = [
        {
            "price": info["price_id"],
            "quantity": 1,
            # `unit_amount` n'est pas envoyé : Stripe le renvoie dans la session, et c'est ce
            # renvoi que la garde monetaire recoupe. Le pretendre ici permettrait au client de
            # choisir son prix.
        }
    ]
    session = stripe_api.session_checkout(
        mode="subscription",
        line_items=lignes,
        client_reference_id=str(user.pk),
        metadata={"user_id": str(user.pk), "palier": palier_code},
        subscription_data={"metadata": {"user_id": str(user.pk), "palier": palier_code}},
        success_url=f"{domaine}/facturation/retour?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{domaine}/facturation?annule=1",
        allow_promotion_codes=True,
        billing_address_collection="if_required",
        # Les portefeuilles: le seul « paiement mobile » disponible sans contrat d'agregateur.
        payment_method_types=list(settings.BILLING_WALLETS) or None,
    )
    ligne = ((session.get("total_details") or {}), (session.get("amount_total")), (session.get("currency")))
    recu = ligne[1]
    ecart = verifier_montant(palier_code, recu, ligne[2])
    if ecart:
        logger.error("facturation ecart de prix sur la session creee: %s", ecart)

    abo = abonnement_ou_creer(user)
    abo.client_id = session.get("customer") or abo.client_id
    abo.prix_id = info["price_id"]
    abo.fournisseur = Fournisseur.STRIPE
    abo.save(update_fields=["client_id", "prix_id", "fournisseur", "maj_le"])
    return {
        "url": session.get("url"),
        "session_id": session.get("id"),
        "expire_le": _epoch(session.get("expires_at")),
        "montant_centimes": info["montant_centimes"],
        "palier": palier_code,
        "wallets": list(settings.BILLING_WALLETS),
    }


def lien_portail(user) -> dict:
    from apps.billing import stripe_api as api

    abo = Abonnement.objects.filter(user=user).first()
    if not abo or not abo.client_id:
        raise api.ErreurStripe(
            "Aucun client Stripe n'est associé à ce compte : le portail n'a rien à montrer.",
            code="client_inconnu",
            statut=409,
        )
    # `return_url` doit etre absolue : Stripe la refuse sinon. Le repli sur une chaine vide produisait
    # un `/facturation` relatif, donc un 400 cote Stripe pour tout client qui ouvrait son portail.
    session = api.session_portail(customer=abo.client_id, return_url=f"{_base_publique()}/facturation")
    return {"url": session.get("url"), "expire_le": _epoch(session.get("expires_at"))}


def verifier_montant_mobile(paiement, *, recu, devise: str | None = None, exige_montant: bool = False) -> str | None:
    """Recoupement du rappel mobile, dans la devise qui a ete facturee — pas en centimes d'euro.

    `None` si tout concorde. La regle d'attente vient de la ligne de paiement (`detail`), ecrite au
    moment de la demande : c'est ce qui rend le controle insensible a une correction ulterieure du
    taux de change.
    """
    from decimal import Decimal

    if recu is None:
        # Un fournisseur qui ne peut pas repondre a `constater()` (demo, staging) n'a rien a oppose au
        # rappel : on ne punit pas un fil de simulation. Des qu'une re-interrogation est possible, en
        # revanche, un rappel sans montant est un motif de refus — c'est le seul chiffre qui prouve.
        return (
            "le rappel ne porte aucun montant : rien a recouper avant d'accorder un palier" if exige_montant else None
        )
    facture = (paiement.detail or {}).get("montant_facture")
    devise_attendue = ((paiement.detail or {}).get("devise_facturee") or "").upper()
    if facture:
        attendu: Decimal | None = Decimal(str(facture))
    else:
        # Ligne anterieure a ce controle (ou agreegateur qui n'a rien facture en devise etrangere) :
        # on revient a la comparaison en centimes, la seule qui ait un sens.
        ecart = verifier_montant(paiement.palier, int(Decimal(str(recu)) * 100), devise)
        return ecart
    try:
        recu_dec = Decimal(str(recu))
    except Exception:  # noqa: BLE001 - un montant illisible est un motif de refus, pas une exception
        return f"montant de rappel illisible : {recu!r}"
    if recu_dec != attendu:
        return f"montant recu {recu_dec} != {attendu} {devise_attendue} attendus pour `{paiement.palier}`"
    if devise and devise_attendue and devise.upper() != devise_attendue:
        return f"devise recue {devise} != {devise_attendue}"
    return None


# ------------------------------------------------------------------ webhook


def appliquer_webhook_stripe(corps: bytes, *, secret_ok: bool) -> dict:
    """Corps déjà vérifié → état appliqué. Renvoie un rapport pour la vue (et pour les tests).

    Les types d'événements inconnus sont **acceptés** (200) et journalisés : répondre 4xx à un
    événement qu'on ne sait pas traiter est la recette pour que Stripe rejoue indéfiniment et finisse
    par désactiver la destination — au premier incident de facturation, on n'aurait plus aucune
    nouvelle.
    """
    if not secret_ok:
        raise stripe_api.ErreurStripe("Signature du webhook invalide.", code="signature_invalide", statut=400)
    evenement = stripe_api.charger_evenement(corps)
    identifiant = evenement.get("id") or ""
    type_evenement = evenement.get("type") or ""
    if not identifiant or not type_evenement:
        raise stripe_api.ErreurStripe("Événement sans identifiant ni type.", code="evenement_incomplet", statut=400)

    with transaction.atomic():
        existant = EvenementPaiement.objects.filter(identifiant=identifiant).first()
        if existant and existant.traite:
            return {"recu": True, "doublon": True, "type": type_evenement}
        journal = existant or EvenementPaiement(identifiant=identifiant, fournisseur=Fournisseur.STRIPE)
        journal.type_evenement = type_evenement
        journal.charge = evenement.get("data") or {}
        journal.erreur = ""
        if type_evenement not in EVENEMENTS_TRAITES:
            journal.traite = True
            journal.erreur = "type non gere"
            journal.save()
            logger.warning("facturation evenement ignore type=%s id=%s", type_evenement, identifiant[:12])
            return {"recu": True, "ignore": True, "type": type_evenement}

        try:
            rapport = _appliquer(type_evenement, journal.charge, journal)
            journal.traite = True
        except stripe_api.ErreurStripe as exc:
            # Erreur metier attendue (price mal regle, compte absent) : on la consigne, on ne rejoue pas.
            journal.erreur = exc.message
            rapport = {"recu": True, "erreur": exc.message}
            logger.error("facturation webhook %s en erreur: %s", type_evenement, exc.message)
        journal.save()
    rapport.setdefault("type", type_evenement)
    return rapport


def _appliquer(type_evenement: str, charge: dict, journal: EvenementPaiement) -> dict:
    objet = (charge or {}).get("object") or {}
    user = _user_de(objet)
    if user is None:
        raise stripe_api.ErreurStripe(
            f"Evenement sans compte reconnaissable (metadata.user_id absent ou invalide) : {type_evenement}",
            code="compte_inconnaissable",
            statut=400,
        )

    if type_evenement == "checkout.session.completed":
        palier = (objet.get("metadata") or {}).get("palier") or ""
        if palier not in plans.PALIERS:
            raise stripe_api.ErreurStripe(
                f"Palier inconnu dans la session : {palier!r}", code="palier_inconnu", statut=400
            )
        ecart = verifier_montant(palier, objet.get("amount_total"), objet.get("currency"))
        if ecart:
            raise stripe_api.ErreurStripe(ecart, code="montant_incoherent", statut=400)
        abonnement_id = objet.get("subscription") or ""
        # La duree initiale est forfaitaire : le premier `invoice.paid` la remplacera par la date reale
        # du fournisseur. Sans ce repli, un webhook `checkout.session.completed` isole le compte sans
        # echeance connue — donc sans grace, sans relance, sans rien.
        periode_fin = timezone.now() + dt.timedelta(days=30)
        abo = accord(
            user,
            palier=palier,
            fournisseur=Fournisseur.STRIPE,
            statut=Statut.ACTIF,
            periode_fin=periode_fin,
            client_id=objet.get("customer") or "",
            abonnement_id=abonnement_id,
            prix_id=((objet.get("lines", {}).get("data") or [{}])[0].get("price") or {}).get("id", ""),
            raison="checkout.session.completed",
        )
        journal.abonnement = abo
        return {"recu": True, "accord": palier}

    if type_evenement in {"customer.subscription.created", "customer.subscription.updated"}:
        statut = {
            "active": Statut.ACTIF,
            "trialing": Statut.ESSAI,
            "past_due": Statut.PAIEMENT_EN_RETARD,
            "unpaid": Statut.PAIEMENT_EN_RETARD,
            "paused": Statut.PAUSE,
            "canceled": Statut.ANNULE,
            "incomplete": Statut.INCOMPLET,
            "incomplete_expired": Statut.ANNULE,
        }.get(objet.get("status", ""), Statut.ACTIF)
        palier = (objet.get("metadata") or {}).get("palier") or user.plan
        prix = (objet.get("items", {}).get("data") or [{}])[0].get("price") or {}
        ecart = verifier_montant(palier, prix.get("unit_amount"), prix.get("currency"))
        if ecart:
            raise stripe_api.ErreurStripe(ecart, code="montant_incoherent", statut=400)
        abo = accord(
            user,
            palier=palier,
            fournisseur=Fournisseur.STRIPE,
            statut=statut,
            periode_fin=_epoch(objet.get("current_period_end")),
            client_id=objet.get("customer") or "",
            abonnement_id=objet.get("id") or "",
            prix_id=prix.get("id", ""),
            en_sursis_jusqu_a=(
                timezone.now() + dt.timedelta(days=_jours_sursis()) if statut == Statut.PAIEMENT_EN_RETARD else None
            ),
            raison=type_evenement,
        )
        journal.abonnement = abo
        return {"recu": True, "statut": statut}

    if type_evenement == "customer.subscription.deleted":
        abo = accord(
            user,
            palier="free",
            fournisseur=Fournisseur.STRIPE,
            statut=Statut.ANNULE,
            periode_fin=None,
            client_id=objet.get("customer") or "",
            abonnement_id=objet.get("id") or "",
            raison="customer.subscription.deleted",
        )
        journal.abonnement = abo
        # Volonte explicite : rien n'est supprime cote client. Un abonnement qui se termine ne doit
        # pas detruire des liens imprimes — on coupe l'acces, on ne casse pas le materiel.
        return {"recu": True, "revoque": True}

    if type_evenement == "invoice.payment_failed":
        abo, _ = Abonnement.objects.get_or_create(user=user)
        abo.statut = Statut.PAIEMENT_EN_RETARD
        abo.en_sursis_jusqu_a = timezone.now() + dt.timedelta(days=_jours_sursis())
        abo.save(update_fields=["statut", "en_sursis_jusqu_a", "maj_le"])
        user.plan_until = user.plan_until or abo.periode_fin
        journal.abonnement = abo
        return {"recu": True, "en_attente": True, "sursis_jusqu_a": abo.en_sursis_jusqu_a}

    if type_evenement == "invoice.payment_succeeded":
        abo, _ = Abonnement.objects.get_or_create(user=user)
        if abo.statut == Statut.PAIEMENT_EN_RETARD:
            abo.statut = Statut.ACTIF
            abo.en_sursis_jusqu_a = None
            abo.save(update_fields=["statut", "en_sursis_jusqu_a", "maj_le"])
        journal.abonnement = abo
        return {"recu": True, "renoue": True}

    return {"recu": True}


def _jours_sursis() -> int:
    from django.conf import settings

    return int(getattr(settings, "BILLING_GRACE_DAYS", 3))


def _user_de(objet: dict):
    from apps.accounts.models import User

    for source in (objet.get("metadata") or {}, (objet.get("subscription_details") or {}).get("metadata") or {}):
        identifiant = source.get("user_id")
        if identifiant:
            return User.objects.filter(pk=identifiant).first()
    client_id = objet.get("customer")
    if client_id:
        return User.objects.filter(abonnement__client_id=client_id).first()
    return None


# ------------------------------------------------------------------ paiement mobile (agregateur)


def demande_paiement_mobile(user, *, palier_code: str, telephone: str = "") -> dict:

    palier_code = (palier_code or "").strip().lower()
    if palier_code not in plans.PALIERS or palier_code == "free":
        raise stripe_api.ErreurStripe("Ce palier n'est pas facturé.", code="palier_infacturable", statut=400)
    fournisseur = mobile_mod.fournisseur_actif()  # 503 si aucun agreegateur
    reference = mobile_mod.nouveau_identifiant()
    montant = plans.palier(palier_code).prix_centimes
    attendu, devise_facturee = fournisseur.montant_attendu(montant)
    resultat = fournisseur.demander_paiement(
        reference=reference,
        montant_centimes=montant,
        telephone=telephone,
        description=f"QR Studio — palier {plans.palier(palier_code).nom}, {montant / 100:.2f} EUR/mois",
        # L'agreegateur a un titulaire : on lui donne l'adresse du compte, pas une adresse generee.
        email=getattr(user, "email", "") or "",
        nom=getattr(user, "display_name", "") or getattr(user, "username", "") or "",
    )
    detail = {"instruction": resultat.instruction, **(resultat.detail or {})}
    # Le montant **reclame a l'instant ou la demande part** est grave dans la ligne : si le taux de
    # change est corrige le lendemain, un rappel d'hier doit etre juge sur ce qui a ete demande, pas
    # sur la valeur du jour (sinon le client qui a paye le juste prix se voit refuser son palier).
    detail.setdefault("montant_facture", str(attendu))
    detail.setdefault("devise_facturee", devise_facturee)
    paiement = PaiementMobile.objects.create(
        reference=reference,
        user=user,
        palier=palier_code,
        montant_centimes=montant,
        telephone=telephone,
        fournisseur=fournisseur.code,
        expire_le=timezone.now() + dt.timedelta(seconds=resultat.expire_en_secondes),
        detail=detail,
    )
    logger.info("facturation demande mobile ref=%s palier=%s user_id=%s", reference, palier_code, user.pk)
    return {
        "reference": paiement.reference,
        "statut": paiement.statut,
        "instruction": resultat.instruction,
        "code_a_utiliser": resultat.code_a_utiliser,
        "expire_le": paiement.expire_le,
        "montant_centimes": montant,
        "devise": devise_facturee,
        "montant_affiche": f"{attendu} {devise_facturee}",
    }


def etat_paiement_mobile(reference: str, user) -> PaiementMobile:
    paiement = PaiementMobile.objects.filter(reference=reference, user=user).first()
    if paiement is None:
        raise stripe_api.ErreurStripe("Référence inconnue pour ce compte.", code="reference_inconnue", statut=404)
    if paiement.statut == PaiementMobile.Statut.EN_ATTENTE and not paiement.encore_valable:
        # L'expiration est materialisee en base, pas seulement calculee a la lecture : sinon la
        # reference reste « en attente » aux yeux de tout le monde et le rappel tardif du fournisseur
        # trouverait une ligne qu'on avait deja decide d'oublier.
        paiement.statut = PaiementMobile.Statut.EXPIRE
        paiement.save(update_fields=["statut"])
    return paiement


def traiter_rappel_mobile(corps: bytes, entete_signature: str | None) -> dict:
    fournisseur = mobile_mod.fournisseur_actif()
    if not fournisseur.verifier_rappel(corps=corps, entete_signature=entete_signature):
        raise stripe_api.ErreurStripe("Signature du rappel invalide.", code="signature_invalide", statut=403)
    rappel = fournisseur.normaliser_rappel(corps)
    reference = rappel.reference
    statut_recu = rappel.statut
    paiement = PaiementMobile.objects.filter(reference=reference).first()
    if paiement is None:
        raise stripe_api.ErreurStripe("Référence de paiement inconnue.", code="reference_inconnue", statut=404)
    if paiement.statut == PaiementMobile.Statut.CONFIRME:
        return {"recu": True, "doublon": True}

    with transaction.atomic():
        paiement.refresh_from_db()
        if paiement.statut != PaiementMobile.Statut.EN_ATTENTE:
            return {"recu": True, "tardif": paiement.statut}
        if statut_recu == "confirmed":
            # Un rappel dit « reussi » n'est pas une preuve : la preuve, c'est ce que le fournisseur
            # confirme quand on le redemande. Sans cette re-interrogation, un POST forge avec une
            # signature valide (ou un rappel rejoue) accorderait un abonnement.
            if fournisseur.reverification_obligatoire:
                constatee = fournisseur.constater(reference)
                if constatee is None or constatee.statut != "confirmed":
                    logger.warning("facturation mobile rappel non confirme par le fournisseur ref=%s", reference)
                    return {"recu": True, "attente_reverification": True}
                montant_recu = constatee.montant
                devise_recue = constatee.devise
            else:
                montant_recu, devise_recue = rappel.montant, rappel.devise
            if paiement.expire_le < timezone.now():
                # Un rappel confirme apres l'echeance : le client avait deja pu relancer une demande.
                # Accorder le palier ici revient a payer deux fois pour la meme periode.
                paiement.statut = PaiementMobile.Statut.EXPIRE
                paiement.detail = {**paiement.detail, "rappel": "confirme hors delai"}
                paiement.save()
                logger.warning("facturation mobile confirme hors delai ref=%s", reference)
                return {"recu": True, "expire": True}
            ecart = verifier_montant_mobile(
                paiement, recu=montant_recu, devise=devise_recue, exige_montant=fournisseur.reverification_obligatoire
            )
            if ecart:
                paiement.statut = PaiementMobile.Statut.ECHECHE
                paiement.detail = {**paiement.detail, "erreur": ecart}
                paiement.save()
                # Pas de levee **dans** le bloc atomic : elle annulerait aussi l'enregistrement du
                # refus, et l'agreegateur aurait confirme un paiement que plus rien ne trace. Le
                # rapport signale `refus`, la vue traduit en 400.
                return {"recu": True, "refus": ecart}
            paiement.statut = PaiementMobile.Statut.CONFIRME
            paiement.confirme_le = timezone.now()
            paiement.save()
            # Duree forfaitaire pour le mobile : l'agreegateur ne connait pas nos periodes. La
            # reconstruction (et le rappel de fin) passent par `billing.renews`.
            accord(
                paiement.user,
                palier=paiement.palier,
                fournisseur=Fournisseur.MOBILE,
                statut=Statut.ACTIF,
                periode_fin=timezone.now() + dt.timedelta(days=30),
                raison=f"mobile:{reference}",
            )
            return {"recu": True, "accord": paiement.palier}
        paiement.statut = PaiementMobile.Statut.ECHECHE
        paiement.detail = {**paiement.detail, "statut_fournisseur": statut_recu}
        paiement.save()
        return {"recu": True, "echec": statut_recu}
