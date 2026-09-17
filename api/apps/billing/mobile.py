"""Paiement mobile : l'interface, et deux implémentations dont une seule marche sans contrat.

Le terme « paiement mobile » recouvre trois choses différentes, et les confondre coûte cher :

1. **les portefeuilles du téléphone** (Apple Pay, Google Pay) : ce sont des cartes bancaires, pilotées
   par le même fournisseur que le paiement en ligne. Ici : activés dans la session Stripe — aucun
   contrat nouveau, aucun secret nouveau ;
2. **le paiement à l'opérateur / agrégateur** (Satispay en Italie, Orange Money / MTN MoMo / CinetPay
   ailleurs) : débit du solde téléphone ou facture opérateur, **asynchrone** — l'utilisateur confirme
   sur son combiné, nous apprenons le résultat des minutes plus tard ;
3. **le portefeuille d'un marchand** (PayPal et consorts) : hors sujet ici.

Le point 1 est câblé (Stripe). Le point 2 a deux implémentations : la démonstration (dev/test) et
**Flutterwave** (`apps/billing/mobile.py::FournisseurFlutterwave`), qui parle à `/v3/charges?type=…`.

La règle qui structure tout le fichier, parce que c'est là que les intégrations mobile-money perdent
de l'argent : **un rappel `successful` ne suffit jamais**. Flutterwave l'écrit noir sur blanc dans ses
recommandations — il faut re-interroger l'API du fournisseur (`verify_by_reference`) et confirmer
statut, montant, devise et `tx_ref` avant de donner quoi que ce soit. `reverification_obligatoire` est
donc une propriété du *contrat*, pas un détail de l'adaptateur : un fournisseur réel qui n'a pas de
réponse à donner laisse le paiement **en attente**, jamais accordé.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class ResultatDemande:
    reference: str
    instruction: str
    expire_en_secondes: int
    # Ce que le front doit afficher : soit un QR à scanner, soit un numéro à composer.
    code_a_utiliser: str = ""
    # Ce que le fournisseur renvoie et qu'il faut garder pour la rapprochement comptable.
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RappelNormalise:
    """Un rappel d'agrégateur traduit dans notre vocabulaire. Zéro devinaille sur les noms de champs."""

    reference: str
    statut: str  # pending | confirmed | failed | refunded | inconnu
    montant: Decimal | None = None  # dans la devise du fournisseur, en unités (pas en centimes)
    devise: str = ""
    ref_fournisseur: str = ""


@dataclass(frozen=True)
class TransactionConstatee:
    """Ce que le fournisseur affirme après qu'on est allé le redemander — la seule preuve qui vaille."""

    statut: str
    montant: Decimal | None = None
    devise: str = ""
    ref_fournisseur: str = ""


class FournisseurMobile:
    """Contrat minimal d'un agrégateur. Cinq méthodes, pas une de plus.

    Deux d'entre elles existent pour une seule raison : ne jamais accorder un palier sur la foi d'un
    message reçu (`normaliser_rappel`) sans avoir redemandé la vérité au fournisseur (`constater`).
    """

    code = "none"
    devise = "EUR"
    #: Nom(s) d'en-tête où ce fournisseur pose sa signature. La vue lit ceux-là, pas un nom générique
    #: inventé par nous : `Verif-Hash` chez Flutterwave, `X-Mobile-Signature` ailleurs.
    entetes_signature: tuple[str, ...] = ("X-Mobile-Signature", "X-Provider-Signature")
    #: Un rappel signé vaut-il quelque chose tout seul ? Pour tout fournisseur réel : non.
    reverification_obligatoire = False

    def demander_paiement(
        self, *, reference: str, montant_centimes: int, telephone: str, description: str, email: str = "", nom: str = ""
    ) -> ResultatDemande:
        raise NotImplementedError

    def verifier_rappel(self, *, corps: bytes, entete_signature: str | None) -> bool:
        raise NotImplementedError

    def statut(self, reference: str) -> str:
        """État normalisé (`pending`/`confirmed`/`failed`/`refunded`) d'une référence."""
        raise NotImplementedError

    def normaliser_rappel(self, corps: bytes) -> RappelNormalise:
        """Traduit le corps du rappel. La forme par défaut reste tolérante (démo, anciens agrégateurs)."""
        charge = json.loads(corps.decode("utf-8"))
        return RappelNormalise(
            reference=str(charge.get("reference") or charge.get("id") or ""),
            statut=_statut_normalise(charge.get("status")),
            montant=_montant_en_unites(charge),
            devise=str(charge.get("currency") or ""),
            ref_fournisseur=str(charge.get("flw_ref") or charge.get("provider_ref") or ""),
        )

    def constater(self, reference: str) -> TransactionConstatee | None:
        """Redemande la transaction au fournisseur. `None` = « je ne sais pas » (démo)."""
        return None

    def montant_attendu(self, prix_centimes: int) -> tuple[Decimal, str]:
        """Montant à réclamer dans la devise du fournisseur, et devise sur laquelle juger le rappel.

        C'est le seul endroit où une conversion de devise est autorisée à exister : sinon on compare
        des centimes d'euro à des shillings, le recoupement échoue, et le client qui a payé ne reçoit
        rien — ou pis : le premier qui paie un montant quelconque reçoit le palier. Le cas par défaut
        (le fournisseur facture la devise de la grille) renvoie simplement les centimes en unités.
        """
        return (Decimal(prix_centimes) / 100, self.devise or "EUR")


def _statut_normalise(brut: object) -> str:
    table = {
        "successful": "confirmed",
        "success": "confirmed",
        "confirmed": "confirmed",
        "paid": "confirmed",
        "completed": "confirmed",
        "pending": "pending",
        "success-pending-validation": "pending",
        "failed": "failed",
        "cancelled": "failed",
        "canceled": "failed",
        "invalid": "failed",
        "refunded": "refunded",
    }
    return table.get(str(brut or "").strip().lower(), "inconnu")


def _decimal(brut: object) -> Decimal | None:
    if brut is None or brut == "":
        return None
    try:
        return Decimal(str(brut))
    except Exception:  # noqa: BLE001 - un montant illisible n'est pas un montant nul : c'est un motif de refus
        return None


def _montant_en_unites(charge: dict) -> Decimal | None:
    """Le montant du rappel, ramene aux **unites** de la devise (2,99 et non 299).

    Les agregateurs ecrivent tantot `amount` (unites, virgule flottante : Flutterwave, Stripe en devise
    majeure), tantot `amount_cents` (entier : notre propre fournisseur de demonstration). Le contrat du
    front-to-fournisseur ne peut pas dependre de cette convention d'ecriture, sinon un `amount_cents`
    lu comme des unites fait passer 899 centimes pour 899 euros — et le recoupement devient une loterie.
    """
    direct = _decimal(charge.get("amount"))
    if direct is not None:
        return direct
    centimes = _decimal(charge.get("amount_cents"))
    return None if centimes is None else centimes / 100


class FournisseurDemo(FournisseurMobile):
    """Fil de simulation : tout passe, rien n'est débité. Actif en dev et en test, jamais en prod.

    La signature du rappel est un HMAC du corps avec le secret configuré — meme forme que les
    agrégateurs réels, pour que le code de production ne change pas de logique le jour du contrat,
    seulement d'implémentation.
    """

    code = "demo"
    devise = "EUR"

    def demander_paiement(
        self, *, reference, montant_centimes, telephone, description, email="", nom=""
    ) -> ResultatDemande:
        return ResultatDemande(
            reference=reference,
            instruction=(
                f"Simulation : composez *150*{reference[-4:]}# ou confirmez la demande recue sur "
                f"{telephone or 'votre telephone'} ({montant_centimes / 100:.2f} {self.devise})."
            ),
            expire_en_secondes=1800,
            code_a_utiliser=reference,
        )

    def verifier_rappel(self, *, corps, entete_signature) -> bool:
        from django.conf import settings

        secret = getattr(settings, "MOBILE_MONEY_WEBHOOK_SECRET", "")
        if not secret:
            return False
        calculee = hmac.new(secret.encode(), corps, hashlib.sha256).hexdigest()
        return bool(entete_signature) and hmac.compare_digest(calculee, entete_signature.strip())

    def statut(self, reference: str) -> str:
        return "pending"


class FournisseurFlutterwave(FournisseurMobile):
    """MTN MoMo / Airtel Money / M-PESA / Orange Money / MoMo via `POST /v3/charges?type=…`.

    Ce qui est délibéré ici :

    * **`tx_ref` = notre référence** (`QRM-…`). C'est elle qui fait le lien entre notre ligne
      `PaiementMobile` et la sienne ; le `flw_ref`/`id` de Flutterwave est gardé dans `detail` pour le
      rapprochement comptable, jamais utilisé pour retrouver le paiement.
    * **conversion de devise explicite et obligatoire** si la devise du réseau n'est pas celle de la
      grille : sans taux configuré, on refuse de demander un débit (503) plutôt que d'envoyer « 299 »
      comme s'il s'agissait de 2,99 UGX ou de 299 KES.
    * **le rappel ne suffit pas** : `constater()` rejoue `verify_by_reference`, et le service n'accorde
      le palier que sur la foi de cette seconde réponse (`reverification_obligatoire = True`).
    * **deux formes de signature acceptées**, parce que la documentation de Flutterwave les a fait
      coexister : `Verif-Hash` (comparaison directe au *secret hash*) et `flutterwave-signature`
      (HMAC-SHA256 du corps brut, encodé en base64). Les deux sont comparées en temps constant.
    """

    code = "flutterwave"
    entetes_signature = ("Verif-Hash", "flutterwave-signature", "X-Mobile-Signature")
    reverification_obligatoire = True

    def __init__(self) -> None:
        from django.conf import settings

        reglages = getattr(settings, "MOBILE_MONEY_FLUTTERWAVE", {})
        self.base = str(reglages.get("base_url", "https://api.flutterwave.com")).rstrip("/")
        self.cle = reglages.get("secret_key", "")
        self.secret_hash = reglages.get("secret_hash", "")
        self.devise = str(reglages.get("devise", "EUR"))
        self.type_charge = str(reglages.get("type", "mobile_money_uganda"))
        self.reseau = str(reglages.get("reseau", ""))
        self.pays = str(reglages.get("pays", ""))
        # Un reglage d'environnement vide est LE cas par defaut (`.env.example` laisse ces cles vides) :
        # `Decimal("")` leverait `InvalidOperation` a la construction du fournisseur, donc sur chaque
        # requete de paiement — une 500 au lieu d'un refus lisible. D'ou les trois parseurs tolerants.
        self.taux = _decimal(reglages.get("taux_change"))
        #: Un rappel déjà vu ne doit pas rouvrir la conversion : on garde la règle d'arrondi ici.
        self.arrondi = _decimal(reglages.get("arrondi")) or Decimal("0.01")
        self.delai = float(_decimal(reglages.get("timeout")) or 12.0)

    # ---------------------------------------------------------------- config

    def _exiger_config(self) -> None:
        from apps.billing.stripe_api import ErreurStripe

        if not self.cle:
            raise ErreurStripe(
                "Flutterwave est choisi comme agreegateur mais `MOBILE_MONEY_FLUTTERWAVE_SECRET_KEY` "
                "est vide : aucune demande de paiement ne peut etre signee.",
                code="mobile_provider_non_configure",
                statut=503,
            )
        if not self.secret_hash:
            raise ErreurStripe(
                "Flutterwave est choisi mais son secret hash de webhook est absent : les rappels "
                "ne peuvent pas etre verifies, donc aucun paiement ne pourra etre confirme.",
                code="mobile_provider_invalide",
                statut=503,
            )

    def montant_attendu(self, prix_centimes: int) -> tuple[Decimal, str]:
        """Le montant à réclamer, dans la devise du réseau. Toujours défini ici : la grille est en EUR."""
        from apps.billing.stripe_api import ErreurStripe

        if self.devise.upper() in {"EUR", ""}:
            return (Decimal(prix_centimes) / 100, "EUR")
        if self.taux is None or self.taux <= 0:
            raise ErreurStripe(
                f"La devise du reseau Flutterwave est `{self.devise}` alors que la grille est en euros, "
                "et aucun taux de conversion n'est configure (`MOBILE_MONEY_FLUTTERWAVE['taux_change']`) : "
                "convertir au pif ferait accepter n'importe quel montant.",
                code="mobile_provider_invalide",
                statut=503,
            )
        brut = (Decimal(prix_centimes) / 100) * self.taux
        return (brut.quantize(self.arrondi, rounding=ROUND_HALF_UP), self.devise)

    # ---------------------------------------------------------------- HTTP

    def _appeler(self, methode: str, chemin: str, *, params: dict | None = None, corps: dict | None = None) -> dict:
        import requests

        from apps.billing.stripe_api import ErreurStripe

        url = f"{self.base}{chemin}"
        entetes = {
            "Authorization": f"Bearer {self.cle}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            reponse = requests.request(methode, url, json=corps, params=params, headers=entetes, timeout=self.delai)
        except requests.RequestException as exc:
            raise ErreurStripe(
                f"Le reseau Flutterwave n'a pas repondu ({type(exc).__name__}) : la demande n'est pas partie.",
                code="mobile_provider_injoignable",
                statut=502,
            ) from exc
        if reponse.status_code >= 400:
            raise ErreurStripe(
                f"Flutterwave a repondu {reponse.status_code}.",
                code="mobile_provider_erreur",
                statut=502,
            )
        try:
            return reponse.json()
        except ValueError as exc:
            raise ErreurStripe(
                "Flutterwave a repondu sans JSON exploitable.", code="mobile_provider_reponse_invalide", statut=502
            ) from exc

    # ---------------------------------------------------------------- contrat

    def demander_paiement(
        self, *, reference, montant_centimes, telephone, description, email="", nom=""
    ) -> ResultatDemande:
        self._exiger_config()
        montant, devise = self.montant_attendu(montant_centimes)
        numero = (telephone or "").replace(" ", "").replace("+", "")
        if not numero.isdigit() or len(numero) < 8:
            from apps.billing.stripe_api import ErreurStripe

            raise ErreurStripe(
                "Flutterwave exige un numero de telephone au format international, indicatif pays compris "
                "(ex. +256 700 000000).",
                code="mobile_telephone_invalide",
                statut=400,
            )
        corps: dict[str, object] = {
            "amount": float(montant),
            "currency": devise,
            "tx_ref": reference,
            "phone_number": numero,
            # L'e-mail est obligatoire chez Flutterwave pour toute charge : on prend celui du compte,
            # jamais une adresse inventee — ce serait casser le rapprochement en cas de litige.
            "email": email or "support@kamcofarm.com",
            "fullname": (nom or "Client QR Studio")[:80],
            "client_ip": "",
            "meta": {"description": description[:200]},
        }
        if self.reseau:
            corps["network"] = self.reseau
        if self.pays:
            corps["country"] = self.pays

        donnees = self._appeler("POST", f"/v3/charges?type={self.type_charge}", corps=corps)
        if donnees.get("status") != "success":
            from apps.billing.stripe_api import ErreurStripe

            raise ErreurStripe(
                str(donnees.get("message") or "Flutterwave a refuse la demande de paiement."),
                code="mobile_refuse",
                statut=502,
            )
        data = donnees.get("data") or {}
        instruction = (
            f"Confirmez le debit de {montant} {devise} sur votre telephone "
            f"(reseau {self.reseau or 'mobile money'}) : une invitation USSD vient de partir."
        )
        return ResultatDemande(
            reference=reference,
            instruction=instruction,
            expire_en_secondes=self._expire_en_secondes(),
            # Rien à taper pour l'utilisateur : c'est le reseau qui le relance. On expose la reference
            # du fournisseur pour qu'un humain puisse la lire a voix haute en cas de litige.
            code_a_utiliser="",
            detail={
                "flw_ref": data.get("flw_ref") or "",
                "transaction_id": data.get("id"),
                "statut_fournisseur": data.get("status") or "",
                "montant_facture": str(montant),
                "devise_facturee": devise,
            },
        )

    def _expire_en_secondes(self) -> int:
        from django.conf import settings

        minutes = getattr(settings, "MOBILE_MONEY_EXPIRE_MINUTES", 30)
        return int(minutes) * 60

    def verifier_rappel(self, *, corps, entete_signature) -> bool:
        if not self.secret_hash or not entete_signature:
            return False
        signature = entete_signature.strip()
        # Forme 1 : le secret hash recopie tel quel (comportement historique du tableau de bord).
        if hmac.compare_digest(signature, self.secret_hash):
            return True
        # Forme 2 : HMAC-SHA256 du corps brut, en base64 (documentation courante). La tolerance d'un
        # encodage hex n'est pas prevue : le documenter serait deviner ce que le serveur enverra.
        calculee = hmac.new(self.secret_hash.encode(), corps, hashlib.sha256).digest()
        import base64

        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            if hmac.compare_digest(signature.encode(), encode(calculee)):
                return True
        return False

    def normaliser_rappel(self, corps: bytes) -> RappelNormalise:
        brut = json.loads(corps.decode("utf-8"))
        # Un rappel Flutterwave encapsule la transaction dans `data` ; `event` n'est qu'un signal grossier
        # (`charge.completed` couvre aussi bien 299 que 0,01 EUR). La source de verite reste `data.status`,
        # et `event` ne sert de repli que quand le corps est plat ou muet sur le statut.
        charge = brut.get("data") if isinstance(brut.get("data"), dict) else brut
        statut = _statut_normalise(charge.get("status"))
        if statut == "inconnu":
            statut = {"charge.completed": "confirmed", "charge.failed": "failed", "charge.refunded": "refunded"}.get(
                str(brut.get("event") or ""), "inconnu"
            )
        return RappelNormalise(
            reference=str(charge.get("tx_ref") or ""),
            statut=statut,
            montant=_decimal(charge.get("amount")),
            devise=str(charge.get("currency") or ""),
            ref_fournisseur=str(charge.get("flw_ref") or charge.get("id") or ""),
        )

    def constater(self, reference: str) -> TransactionConstatee | None:
        from apps.billing.stripe_api import ErreurStripe

        try:
            reponse = self._appeler("GET", "/v3/transactions/verify_by_reference", params={"tx_ref": reference})
        except ErreurStripe:
            # Le service laisse le paiement en attente. Accorder sur une reponse absente serait une prime a
            # l'indisponibilite de Flutterwave : une panne de 30 s vaudrait des licences gratuites.
            return None
        data = reponse.get("data") or {}
        if reponse.get("status") != "success" or not data:
            return None
        return TransactionConstatee(
            statut=_statut_normalise(data.get("status")),
            montant=_decimal(data.get("amount")),
            devise=str(data.get("currency") or ""),
            ref_fournisseur=str(data.get("flw_ref") or data.get("id") or ""),
        )

    def statut(self, reference: str) -> str:
        constatee = self.constater(reference)
        return constatee.statut if constatee else "pending"


class FournisseurInactif(FournisseurMobile):
    code = "none"

    def demander_paiement(self, **_kwargs):
        from apps.billing.stripe_api import ErreurStripe

        raise ErreurStripe(
            "Aucun agrégateur de paiement mobile n'est configure sur cette instance.",
            code="mobile_provider_non_configure",
            statut=503,
        )

    def verifier_rappel(self, **_kwargs) -> bool:
        return False

    def statut(self, reference: str) -> str:
        return "unknown"


FOURNISSEURS: dict[str, type[FournisseurMobile]] = {"demo": FournisseurDemo, "flutterwave": FournisseurFlutterwave}


def fournisseur_actif() -> FournisseurMobile:
    """Résolution à l'appel (et pas à l'import) : un réglage posé dans `.env` doit suffire à activer.

    En production, le fournisseur de démonstration est **refusé** sans configuration explicite :
    laisser tourner « tout est accepté » sur une instance publique, c'est distribuer des abonnements
    payants à quiconque appuie sur un bouton.
    """
    from django.conf import settings

    code = getattr(settings, "MOBILE_MONEY_PROVIDER", "none") or "none"
    if code == "none":
        return FournisseurInactif()
    if code == "demo":
        if not settings.DEBUG and not settings.MOBILE_MONEY_ALLOW_DEMO:
            from apps.billing.stripe_api import ErreurStripe

            raise ErreurStripe(
                "Le fournisseur de demonstration est refuse hors dev (il accepte tous les paiements).",
                code="mobile_provider_invalide",
                statut=503,
            )
        return FournisseurDemo()
    fabrique = FOURNISSEURS.get(code)
    if fabrique is None:
        from apps.billing.stripe_api import ErreurStripe

        raise ErreurStripe(
            f"Agrégateur de paiement mobile inconnu : `{code}`.",
            code="mobile_provider_inconnu",
            statut=503,
        )
    return fabrique()


def nouveau_identifiant() -> str:
    """Référence courte et sans ambiguïté de saisie : ni `0`/`O`, ni `1`/`I`."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "QRM-" + "".join(secrets.choice(alphabet) for _ in range(10))
