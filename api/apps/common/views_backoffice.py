"""Back-office métier (`/manage/`) : état des lieux, analytique des scans, création gratuite, abonnements.

Trois principes commandent ces vues, et ils expliquent pourquoi elles ne sont pas dans `django-admin` :

1. **Elles lisent les agrégats, jamais la table brute.** Les pages d'analytique passent par
   `apps/analytics/dashboard.py`, dont chaque requête est bornée (une ligne par jour pour les totaux
   plateforme, un `LIMIT` trié côté SQL pour les classements). Une page d'admin qui fait `SUM()` sur
   `scan_events` est un incident par consultation.
2. **La création gratuite n'est pas un chemin à part.** `qr_creer` construit un `QrSerializer` et appelle
   `apps.qr.services.create_qr()` — exactement ce que fait l'API. L'exemption de facturation vit donc dans
   un seul endroit (`apps/qr/quota.py`, règle dans `apps/accounts/exemption.py`) : si un trou se déclare
   entre « gratuit pour l'admin » et « facturé au client », il ne peut pas être dans le contrôleur.
3. **Les permissions sont nommées.** Superutilisateur = tout ; un membre du personnel ne voit l'analytique
   qu'avec `analytics.voir_analytique_plateforme` et ne crée gratuitement qu'avec `qr.creer_sans_facturation`.
   « Est du staff » ne doit jamais vouloir dire « ne paie rien ».
"""

from __future__ import annotations

import base64
import datetime as dt
from functools import wraps

from django.contrib.auth.decorators import permission_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.analytics import dashboard as analytique_donnees
from apps.common import cache_tools, charts, streams


def personnel_requis(user) -> bool:
    """Le minimum pour poser un pied dans `/manage/` : actif et membre du personnel."""
    return bool(user and user.is_active and user.is_staff)


def acces_admin(view):
    """`staff_member_required`, corrige de la boucle de redirection.

    Le decorateur de Django redirige **toujours** vers `login_url` quand le test echoue. Or ici la page de
    connexion est celle d'allauth, qui ejecte un deja-connecte vers sa destination : un compte client
    authentifie qui tape `/manage/` part en aller-retour infini (mesure : `ERR_TOO_MANY_REDIRECTS` dans le
    navigateur, et c'est le front qui l'a trouve, pas les tests backend). Un connecte qui n'est pas du
    personnel recoit donc un 403 ; seul l'anonyme est envoye vers la connexion.
    """

    @wraps(view)
    def _vue(request, *args, **kwargs):
        utilisateur = getattr(request, "user", None)
        if personnel_requis(utilisateur):
            return view(request, *args, **kwargs)
        if utilisateur is not None and getattr(utilisateur, "is_authenticated", False):
            raise PermissionDenied("Espace reserve au personnel de l'instance.")
        return redirect_to_login(request.get_full_path(), "/accounts/login/", "next")

    return _vue


@acces_admin
def dashboard(request: HttpRequest) -> HttpResponse:
    from apps.accounts.models import User
    from apps.analytics.models import PlatformDailyStats
    from apps.qr.models import QrCode

    hier = dt.date.today() - dt.timedelta(days=1)
    vigile = PlatformDailyStats.objects.filter(day=hier).first()
    contexte = {
        "users": User.objects.count(),
        "users_actifs": User.objects.filter(is_active=True).count(),
        "qr_total": QrCode.objects.count(),
        "qr_dynamic": QrCode.objects.filter(kind="dynamic").count(),
        "qr_paused": QrCode.objects.filter(is_active=False).count(),
        "qr_admin": QrCode.objects.filter(origine="admin").count(),
        "scans_hier": vigile.scans if vigile else None,
        "uniques_hier": vigile.visiteurs_uniques if vigile else None,
        "bots_hier": vigile.bots_bloques if vigile else None,
        "agregat_jour": vigile.day.isoformat() if vigile else None,
        "top_qr": list(
            QrCode.objects.filter(deleted_at__isnull=True)
            .order_by("-scan_count_total")
            .values("id", "slug", "label", "kind", "origine", "scan_count_total")[:10]
        ),
        "cache": "ok" if cache_tools.put("backoffice:ping", 1, 30) else "degrade",
    }
    return render(request, "manage/dashboard.html", contexte)


@acces_admin
def queue_status(request: HttpRequest) -> HttpResponse:
    from django.conf import settings

    return render(
        request,
        "manage/queue.html",
        {
            "stream_enabled": streams.is_enabled(),
            "stream_length": streams.stream_length(),
            "stream_name": (settings.QR or {}).get("SCAN_STREAM", "stream:scans"),
            "broker": settings.CELERY_BROKER_URL,
        },
    )


# --------------------------------------------------------------------------- analytique globale


@acces_admin
@permission_required("analytics.voir_analytique_plateforme", raise_exception=True)
def analytique(request: HttpRequest) -> HttpResponse:
    jours = _entier(request.GET.get("jours"), defaut=30)
    kind = request.GET.get("kind") or ""
    origine = request.GET.get("origine") or ""
    since, until = analytique_donnees.periode(jours)
    filtres = {"kind": kind, "origine": origine}

    serie = analytique_donnees.serie(since=since, until=until, **filtres)
    totaux = analytique_donnees.totaux(since=since, until=until, **filtres)
    pays = analytique_donnees.pays(since=since, until=until, limit=10, **filtres)
    labels = [ligne["day"][5:] for ligne in serie]

    def trace(cle: str, libelle: str, couleur: str) -> dict:
        return {"libelle": libelle, "couleur": couleur, "points": [ligne[cle] for ligne in serie]}

    contexte = {
        "jours": jours,
        "jours_disponibles": analytique_donnees.JOURS_DISPONIBLES,
        "kind": kind,
        "origine": origine,
        "since": since,
        "until": until,
        "totaux": totaux,
        "pays": pays,
        "nb_jours": len(serie),
        # Le volume de la periode, pas seulement une moyenne : une moyenne masque un trou de trois jours.
        "moyenne_jour": round(totaux["scans"] / max(len(serie), 1)),
        "graph_scans": charts.courbe(
            [trace("scans", "Scans", "#2563eb"), trace("unique", "Visiteurs uniques", "#0d9488")],
            labels=labels,
            titre=f"Scans et visiteurs uniques sur {len(serie)} jours",
        ),
        "graph_canaux": charts.courbe(
            [trace("mobile", "Mobile", "#b45309"), trace("desktop", "Desktop", "#7c3aed")],
            labels=labels,
            titre="Répartition par appareil",
        ),
        "graph_mix": charts.courbe(
            [
                trace("dynamique", "QR dynamiques", "#2563eb"),
                trace("statique", "QR statiques", "#0d9488"),
                trace("admin", "Dont espace admin", "#dc2626"),
            ],
            labels=labels,
            titre="Mélange statique / dynamique et part de l'espace admin",
        ),
        "graph_pays": charts.barres([(ligne["nom"], ligne["scans"]) for ligne in pays], titre="Scans par pays"),
        "top": analytique_donnees.top_qrs(since=since, until=until, limit=15, **filtres),
        "creations": analytique_donnees.volumetrie_creations(since=since, until=until),
        "filtre_actif": bool(kind or origine),
        "recalcule": _entier(request.GET.get("recalcule"), defaut=-1),
    }
    return render(request, "manage/analytique.html", contexte)


# --------------------------------------------------------------------------- drill-down par QR


@acces_admin
@permission_required("analytics.voir_analytique_plateforme", raise_exception=True)
def analytique_qr(request: HttpRequest, pk: int) -> HttpResponse:
    from apps.qr.models import QrCode

    qr = get_object_or_404(QrCode.objects.select_related("owner"), pk=pk)
    details = analytique_donnees.par_qr(qr)
    serie = details["serie"]
    contexte = {
        "qr": qr,
        "details": details,
        "totaux": details["totaux"],
        "graph": charts.courbe(
            [
                {"libelle": "Scans", "couleur": "#2563eb", "points": [ligne["scans"] for ligne in serie]},
                {"libelle": "Visiteurs uniques", "couleur": "#0d9488", "points": [ligne["unique"] for ligne in serie]},
            ],
            labels=[ligne["day"][5:] for ligne in serie],
            titre=f"Scans du QR {qr.slug}",
        ),
        "graph_pays": charts.barres(
            [(ligne["nom"], ligne["scans"]) for ligne in details["pays"]],
            titre="Scans par pays",
            couleur="#0d9488",
        ),
        "aperco": _aperco(qr),
        "cree": request.GET.get("cree") == "1",
        "supprime": request.GET.get("supprime") == "1",
    }
    return render(request, "manage/qr_detail.html", contexte)


def _aperco(qr) -> str:
    """Un `<img>` en data URI, pas du SVG inline : les couleurs de design viennent d'un JSON utilisateur.

    L'aperçu est regénéré à la volée (320 px) plutôt que lu du cache : sur une page d'admin on veut l'état
    courant, pas la dernière version servie aux visiteurs.
    """
    from apps.qr.render import rendu

    octets, _, _ = rendu(qr, fmt="svg", size=320)
    return "data:image/svg+xml;base64," + base64.b64encode(octets).decode("ascii")


# --------------------------------------------------------------------------- création gratuite


@acces_admin
@permission_required("qr.creer_sans_facturation", raise_exception=True)
def qr_creer(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        return _creer(request)
    contexte = _base_creation(valeurs={"kind": "static", "type_id": "url", "is_public": "on"})
    # `?cree=<id>&promo=<code>` : la confirmation passe par l'URL (PRG) et non par un re-render du POST —
    # un agent qui recharge la page ne doit pas creer un deuxieme QR, mais doit pouvoir relire le code
    # promo qui vient d'etre genere (c'est la seule fois ou il s'affiche en clair).
    cree = (request.GET.get("cree") or "").strip()
    if cree.isdigit():
        from apps.qr.models import QrCode

        contexte["cree"] = QrCode.objects.filter(pk=int(cree)).first()
        contexte["promo"] = (request.GET.get("promo") or "").strip()[:48]
    return render(request, "manage/qr_creer.html", contexte)


def _base_creation(**extra) -> dict:
    from django.conf import settings

    # La limite est lue du reglage, pas recopiee dans le gabarit : `QrSerializer.validate_payload` applique
    # exactement la meme valeur, et un gabarit qui annoncerait 2953 octets alors que l'instance en autorise
    # 1500 ment au premier qui lit la page.
    from apps.qr import art
    from apps.qr.types import KNOWN_TYPE_IDS

    return {
        "types": sorted(KNOWN_TYPE_IDS),
        "limite_payload": (settings.QR or {}).get("MAX_PAYLOAD_BYTES", 2953),
        # Les styles viennent du module de rendu, pas d'une liste de gabarit : quand un contraste change,
        # l'admin et l'API bougent ensemble.
        "styles": art.styles_disponibles(),
        "logo_surface_max": art.LOGO_SURFACE_MAX,
        **extra,
    }


def _creer(request: HttpRequest) -> HttpResponse:
    from apps.qr import services
    from apps.qr.serializers import QrSerializer

    donnees = {
        "kind": request.POST.get("kind") or "static",
        "type_id": request.POST.get("type_id") or "url",
        "label": (request.POST.get("label") or "").strip(),
        "notes": (request.POST.get("notes") or "").strip(),
        "payload": request.POST.get("payload") or "",
        "target_url": request.POST.get("target_url") or "",
        "is_public": request.POST.get("is_public") == "on",
    }
    # Le dessin est une option du palier Entreprise, et `design` est une boite noire bornee par le
    # serializer : on n'invente pas ici un deuxieme format de reglages, on remplit celui du studio.
    design: dict[str, object] = {}
    if (request.POST.get("art") or "").strip():
        design["art"] = request.POST["art"].strip().lower()
    if (request.POST.get("animation") or "").strip():
        design["animation"] = request.POST["animation"].strip().lower()
    for champ in ("frames", "liser", "duree"):
        brut = (request.POST.get(champ) or "").strip()
        if brut.isdigit():
            design[champ] = int(brut)
    if design:
        donnees["design"] = design
    # Une erreur par champ, dans le format du reste de l'API : la page d'admin doit dire la même phrase
    # qu'un client qui appelle `POST /qr/codes`, sinon l'équité du chemin partagé n'est plus vérifiable.
    serie = QrSerializer(data=donnees)
    if not serie.is_valid():
        contexte = _base_creation(
            valeurs={**donnees, "is_public": "on" if donnees["is_public"] else ""},
            global_erreur=_message_global(serie.errors),
        )
        contexte["erreurs"] = {champ: liste for champ, liste in serie.errors.items() if champ != "__all__"}
        return render(request, "manage/qr_creer.html", contexte, status=400)

    qr = services.create_qr(owner=request.user, serializer=serie)
    # La provenance s'écrit **après** `create_qr`, pas dans le serializer : `origine` n'est pas une donnée
    # qu'un client a le droit de choisir — il pourrait se faire passer pour l'admin. Seul ce chemin, gated
    # par la permission, pose `admin`.
    qr.origine = "admin"
    qr.save(update_fields=["origine", "updated_at"])
    _journaliser(request, qr, "Créé depuis l'espace admin, sans facturation.")

    promo = ""
    if (request.POST.get("promo_jours") or "").strip().isdigit():
        promo = _offre_admin(request, qr)
    if promo:
        # Le code n'existe qu'une fois : le remettre dans l'URL, c'est le seul moyen pour l'agent de le
        # communiquer sans rouvrir la base.
        return HttpResponseRedirect(reverse("backoffice:qr_creer") + f"?cree={qr.pk}&promo={promo}")
    return HttpResponseRedirect(reverse("backoffice:analytique_qr", args=[qr.pk]) + "?cree=1")


def _offre_admin(request: HttpRequest, qr) -> str:
    """Code promo généré pour la campagne créée à la main : mêmes règles que l'API, palier nonobstant.

    L'admin n'est pas tenu par `codes_promo_max` (il ne facture rien, `exonere_de_facturation`), mais il
    passe par `promo.generer_code` et par la contrainte d'unicite : un code tape a la main ici serait un
    code livre sans date de fin, c'est-a-dire exactement ce que le produit refuse.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.qr import promo as regles
    from apps.qr.models import PromoCode

    jours = max(1, min(int(request.POST["promo_jours"]), 730))
    valeur = (request.POST.get("promo_valeur") or "").strip().replace(",", ".")
    libelle = (request.POST.get("promo_libelle") or "").strip()[:160]
    usages = (request.POST.get("promo_usages_max") or "").strip()
    code = regles.generer_code(libelle=libelle)
    ligne = PromoCode.objects.create(
        owner=qr.owner,
        qr=qr,
        code=code,
        libelle=libelle,
        # `pourcentage` avec une valeur, sinon `acces` : « la remise » sans chiffre n'est pas une remise.
        remise_type="pourcentage" if valeur.replace(".", "", 1).isdigit() else "acces",
        remise_valeur=valeur if valeur.replace(".", "", 1).isdigit() else None,
        expire_le=timezone.now() + timedelta(days=jours),
        usages_max=int(usages) if usages.isdigit() else None,
        notes="Généré depuis l'espace admin.",
    )
    _journaliser(request, qr, f"Code promo {ligne.code} généré pour {jours} jour(s).")
    return ligne.code


def _message_global(erreurs: dict) -> str:
    if "__all__" in erreurs:
        return " ; ".join(str(e) for e in erreurs["__all__"])
    for champ in ("payload", "target_url", "type_id", "kind"):
        if champ in erreurs:
            return f"{champ} : {erreurs[champ][0]}"
    return "Le formulaire est incomplet."


def _journaliser(request: HttpRequest, objet, message: str) -> None:
    """Trace d'audit : qui a fait quoi de gratuit. `LogEntry` suffit, et il est déjà là."""
    from django.contrib.admin.models import ADDITION, LogEntry
    from django.contrib.contenttypes.models import ContentType

    if not request.user.is_authenticated:  # la vue est deja staff-gated ; le journal, lui, exige un id
        raise PermissionDenied("Le journal d'audit necessite un utilisateur authentifie.")

    LogEntry.objects.create(
        user=request.user,
        content_type=ContentType.objects.get_for_model(objet),
        object_id=str(objet.pk),
        object_repr=str(objet)[:200],
        action_flag=ADDITION,
        change_message=message,
    )


@require_POST
@acces_admin
@permission_required("qr.creer_sans_facturation", raise_exception=True)
def qr_supprimer(request: HttpRequest, pk: int) -> HttpResponse:
    """Suppression logique par le même service que l'API : pas de `delete()` maison dans une vue d'admin."""
    from apps.qr import services
    from apps.qr.models import QrCode

    qr = get_object_or_404(QrCode.objects.select_related("owner"), pk=pk)
    services.soft_delete(qr, actor=request.user)
    return HttpResponseRedirect(reverse("backoffice:analytique_qr", args=[qr.pk]) + "?supprime=1")


# --------------------------------------------------------------------------- abonnements


@acces_admin
@permission_required("analytics.voir_analytique_plateforme", raise_exception=True)
def abonnements(request: HttpRequest) -> HttpResponse:
    from django.db.models import Count

    from apps.billing.models import Abonnement, EvenementPaiement, PaiementMobile
    from apps.qr.models import QrCode

    lignes = list(
        Abonnement.objects.select_related("user")
        .order_by("-maj_le")[:200]
        .values("user__email", "palier", "statut", "fournisseur", "periode_fin", "en_sursis_jusqu_a", "maj_le")
    )
    # `Count("pk")` et non `Count("id")` : `Abonnement` a pour cle primaire son OneOTO vers `User`, il n'a
    # donc pas de colonne `id` — la mesure est faite sur la page rendue (`FieldError: Cannot resolve 'id'`).
    par_statut = dict(Abonnement.objects.values("statut").annotate(n=Count("pk")).values_list("statut", "n"))
    return render(
        request,
        "manage/abonnements.html",
        {
            "lignes": lignes,
            "par_statut": par_statut,
            "evenements": list(
                EvenementPaiement.objects.order_by("-recu_le")[:40].values(
                    "identifiant", "type_evenement", "recu_le", "traite", "erreur"
                )
            ),
            "mobiles": list(
                PaiementMobile.objects.select_related("user")
                .order_by("-cree_le")[:25]
                .values("reference", "user__email", "palier", "statut", "montant_centimes", "telephone", "cree_le")
            ),
            "qr_admin": QrCode.objects.filter(origine="admin").count(),
        },
    )


# --------------------------------------------------------------------------- recalcul des agrégats


@require_POST
@acces_admin
@permission_required("analytics.voir_analytique_plateforme", raise_exception=True)
def recalcul(request: HttpRequest) -> HttpResponse:
    """Reconstruit les agrégats des N derniers jours, depuis le navigateur.

    Borné à 30 jours volontairement : au-delà c'est `python manage.py rebuild_daily_stats` qu'il faut
    lancer, parce qu'une vue HTTP n'a rien à faire de tenir deux minutes de SQL agrégé.
    """
    from apps.analytics.aggregates import rebuild_for_day

    jours = min(max(_entier(request.POST.get("jours"), defaut=7), 1), 30)
    ecrit = 0
    today = dt.date.today()
    for decalage in range(jours):
        ecrit += rebuild_for_day(today - dt.timedelta(days=decalage))
    from apps.analytics.models import PlatformDailyStats

    _journaliser(request, PlatformDailyStats, f"Reconstruction manuelle sur {jours} jours : {ecrit} lignes.")
    return HttpResponseRedirect(reverse("backoffice:analytique") + f"?jours={jours}&recalcule={ecrit}")


def _entier(brut: object, *, defaut: int) -> int:
    """Un filtre d'URL n'est pas une entree de confiance : `?jours=abc` doit retomber sur le defaut."""
    if isinstance(brut, int) and not isinstance(brut, bool):
        return brut
    if isinstance(brut, str) and brut.strip().lstrip("-").isdigit():
        return int(brut.strip())
    return defaut
