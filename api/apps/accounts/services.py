"""Logique de compte : politique de mot de passe, e-mails, verrouillage.

Séparé des vues pour que le même garde-fou s'applique à `createsuperuser`, à l'API et à une
importation future — un contrôle placé dans un sérialiseur ne protège pas le shell.
"""

from __future__ import annotations

import hashlib
import logging
import re

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from apps.accounts.models import EmailVerificationCode, User

logger = logging.getLogger(__name__)


class PolicyError(ValidationError):
    """Erreur de politique de mot de passe, avec un code machine."""


def check_password_strength(password: str, *, user: User | None = None) -> None:
    """Applique `AUTH_PASSWORD_VALIDATORS` (longueur 12, communs, numeriques seuls, similarité)."""
    try:
        validate_password(password, user)
    except ValidationError as exc:
        raise PolicyError(list(exc.messages)) from exc


def consent_fingerprint(ip: str | None) -> str:
    """Trace non réversible de l'IP au moment du consentement : la preuve RGPD, pas une donnée perso."""
    if not ip:
        return ""
    salt = settings.SECRET_KEY[-16:]
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:32]


def send_verification_email(user: User, code: EmailVerificationCode) -> str:
    # Le pied de message ne doit pas inventer une deuxieme adresse : sans ce repli, un `support@...`
    # hardcode ailleurs dans le texte divergerait de l'expediteur reel (itsupport@), et l'`if` evite
    # aussi un AttributeError quand `DEFAULT_FROM_EMAIL` est malforme — on laisse alors le pied vide.
    destinataire_support = settings.SUPPORT_EMAIL
    if not destinataire_support:
        correspondance = re.search(r"[\w.+-]+@[\w.-]+", settings.DEFAULT_FROM_EMAIL)
        destinataire_support = correspondance.group(0) if correspondance else ""

    context = {
        "user": user,
        "code": code.code,
        "minutes": int(code.VALID_FOR.total_seconds() // 60),
        "brand": "QR Studio",
        # Vide par defaut => la boite qui envoie vraiment. Voir la note sur SUPPORT_EMAIL dans le settings.
        "support_email": destinataire_support,
    }
    text = render_to_string("emails/verify.txt", context)
    html = render_to_string("emails/verify.html", context)
    message = EmailMultiAlternatives(
        subject="Confirmez votre adresse e-mail",
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        # Les deux en-tetes ci-dessous evitent un orage de reponses automatiques: une boite de
        # verification sans `Auto-Submitted` recoit les vacataires de tous les clients, et chaque
        # reponse relance une notification. `Reply-To` n'est pas pose: il est identique au `From`
        # (itsupport@), et le doubler est ce que filtrent certains anti-spam.
        headers={"Auto-Submitted": "auto-generated", "Precedence": "bulk"},
        to=[user.email],
    )
    message.attach_alternative(html, "text/html")
    message.send(fail_silently=False)
    logger.info("email de verification envoye user_id=%s", user.pk)
    return "envoyé"


def send_password_reset_email(user: User, uidb64: str, token: str) -> None:
    from django.utils.http import urlsafe_base64_encode  # noqa: F401 (réexport pratique pour tests)

    base = (settings.QR or {}).get("SHORT_BASE_URL", "http://localhost:8000")
    link = f"{base}/reset-password?uid={uidb64}&token={token}"
    context = {"user": user, "link": link, "hours": 3, "brand": "QR Studio"}
    text = render_to_string("emails/password_reset.txt", context)
    html = render_to_string("emails/password_reset.html", context)
    message = EmailMultiAlternatives(
        subject="Réinitialisation de votre mot de passe",
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        # Les deux en-tetes ci-dessous evitent un orage de reponses automatiques: une boite de
        # verification sans `Auto-Submitted` recoit les vacataires de tous les clients, et chaque
        # reponse relance une notification. `Reply-To` n'est pas pose: il est identique au `From`
        # (itsupport@), et le doubler est ce que filtrent certains anti-spam.
        headers={"Auto-Submitted": "auto-generated", "Precedence": "bulk"},
        to=[user.email],
    )
    message.attach_alternative(html, "text/html")
    message.send(fail_silently=False)


def mark_login_success(user: User, *, via: str = "password") -> None:
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    logger.info("login reussi user_id=%s via=%s", user.pk, via)


def touch_last_login(user) -> None:
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    logger.info("login reussi user_id=%s", user.pk)


# --------------------------------------------------------------------- RGPD (export, droit à l'effacement)


EXPORT_LIMITE_LIGNES = 5000


def _comptes_sociaux(user) -> list[dict]:
    try:
        from allauth.socialaccount.models import SocialAccount
    except ImportError:  # pragma: no cover - dependance optionnelle selon le deploiement
        return []
    return [
        {"fournisseur": compte.provider, "identifiant_fournisseur": compte.uid, "lie_le": compte.date_joined}
        for compte in SocialAccount.objects.filter(user=user)
    ]


def export_account(user) -> dict:
    """Portabilité: tout ce que le service sait sur ce compte, en un seul objet JSON.

    Le format est versionné (`format`) parce qu'un client qui archive son export veut pouvoir le
    relire dans cinq ans: sans clé de version, le moindre changement de champ rend l'archive
    silencieusement incohérente. Les QR supprimés *logiquement* sont inclus — ils font partie des
    données que la personne a produites, et « mes flyers retirés ont-ils été supprimés du service ? »
    est justement la question qui amène un client à demander un export.
    """
    from apps.analytics.models import QrDailyStats
    from apps.qr.models import QrCode, QrReport, QrVersion

    qr_ids = list(QrCode.objects.filter(owner=user).values_list("id", flat=True))
    compte = {
        "email": user.email,
        "date_creation": user.date_joined,
        "derniere_connexion": user.last_login,
        "plan": user.plan,
        "langue": user.locale,
        "fuseau": user.timezone_name,
        "emails_marketing": user.marketing_opt_in,
        "email_verifie": user.is_email_verified,
        "consentement_mesure": {
            "accorde": user.consent_tracking_at is not None,
            "horodatage": user.consent_tracking_at,
            # L'empreinte est conservée: c'est la preuve que le consentement a été donné depuis une
            # adresse donnée, sans que l'adresse elle-même survive au temps.
            "empreinte_ip": user.consent_ip_hash,
        },
        # Nom de relation reel d'allauth (`socialaccount_set`, pas `social_accounts`): se fier a un
        # `hasattr` sur un nom invente ferait sortir un export *sans* la ligne Google, et le client
        # croirait n'avoir jamais autorise le fournisseur.
        "connexion_sociale": _comptes_sociaux(user),
    }
    stats = list(
        QrDailyStats.objects.filter(qr_id__in=qr_ids)
        .order_by("day", "country_code")
        .values(
            "qr_id", "day", "country_code", "scans", "unique_visitors", "mobile", "desktop", "tablet", "bot_blocked"
        )[:EXPORT_LIMITE_LIGNES]
    )
    return {
        "format": "qrs-export/1",
        "genere_le": timezone.now().isoformat(),
        "compte": compte,
        "qr": list(
            QrCode.objects.filter(owner=user)
            .order_by("-created_at")
            .values(
                "id",
                "slug",
                "kind",
                "type_id",
                "label",
                "notes",
                "payload",
                "target_url",
                "design",
                "is_active",
                "redirect_mode",
                "scan_count_total",
                "created_at",
                "updated_at",
                "archived_at",
                "deleted_at",
            )[:EXPORT_LIMITE_LIGNES]
        ),
        "historique_modifications": list(
            QrVersion.objects.filter(qr_id__in=qr_ids)
            .order_by("-created_at")
            .values("qr_id", "created_at", "change")[:EXPORT_LIMITE_LIGNES]
        ),
        "statistiques": stats,
        "signalements_recus": list(
            QrReport.objects.filter(qr_id__in=qr_ids)
            .order_by("-created_at")
            .values("qr_id", "created_at", "reason", "status")[:EXPORT_LIMITE_LIGNES]
        ),
    }


def count_export_rows(user) -> int:
    """Taille de l'export, avant de le construire: au-dela de la limite on renvoie un 413 explicite."""
    from apps.analytics.models import QrDailyStats
    from apps.qr.models import QrCode, QrVersion

    ids = list(QrCode.objects.filter(owner=user).values_list("id", flat=True))
    return (
        len(ids) + QrVersion.objects.filter(qr_id__in=ids).count() + QrDailyStats.objects.filter(qr_id__in=ids).count()
    )


def delete_account(user, *, actor=None) -> dict:
    """Effacement complet: le compte, ses QR, leur historique, leurs aggregats et leurs lignes brutes.

    Ordre et choix deliberes:

    * on supprime les QR *durs* (pas `deleted_at`): une suppression logique laisse la ligne, donc le
      droit a l'effacement ne serait pas satisfait — le caractere « je veux disparaitre » n'est pas le
      meme que « je range ce flyer » ;
    * les lignes brutes de scan sont supprimees par `owner_id`, pas par partition: la table est
      partitionnee par `ts` et un `DELETE` par proprietaire y scanne chaque partition. C'est rare
      (un compte efface par mois en moyenne) et cote un coup de CPU, la purge de routine reste le
      `DROP PARTITION` de `purge_expired_ips` ;
    * rien n'est ecrit dans le journal avec l'e-mail: un log n'est pas un endroit ou faire survivre
      une donnee personnelle a sa suppression.

    Retourne les comptes supprimes — affiches cote personnel, jamais cote client.
    """
    from django.db import transaction

    from apps.analytics.models import QrDailyStats, ScanEvent
    from apps.qr.models import QrCode, QrReport, QrVersion

    with transaction.atomic():
        ids = list(QrCode.objects.filter(owner=user).values_list("id", flat=True))
        rapports = QrReport.objects.filter(qr_id__in=ids).delete()[0] if ids else 0
        versions = QrVersion.objects.filter(qr_id__in=ids).delete()[0] if ids else 0
        aggregats = QrDailyStats.objects.filter(qr_id__in=ids).delete()[0] if ids else 0
        # Les lignes brutes sont attachees au compte par `owner_id` (colonnes reelles, pas de cle
        # etrangere: la table est partitionnee). On supprime par proprietaire meme sans QR: un scan
        # peut exister sur un QR deja dur-supprime.
        bruts = ScanEvent.objects.filter(owner_id=user.pk).delete()[0]
        # Pas de table « modèles de design » cote serveur: le design est une colonne JSON du QR
        # (`QrCode.design`), donc il disparait avec la ligne. Les references `actor` / `reviewed_by`
        # des lignes gardees (rapports de moderation) sont en SET_NULL par le modele: elles ne
        # conservent aucune identite, seulement le fait qu'un compte existait.
        qrs = QrCode.objects.filter(owner=user).delete()[0]
        user.delete()

    bilan = {
        "utilisateur": 1,
        "qr": qrs,
        "versions": versions,
        "rapports": rapports,
        "agregats": aggregats,
        "scans_bruts": bruts,
        # Le bilan est renvoye a l'appelant (vue) pour la confirmation cote client; l'identite de
        # l'acteur, elle, ne quitte pas le journal.
    }
    logger.info(
        "compte efface user_id=%s qr=%s scans_bruts=%s par_utilisateur=%s",
        user.pk,
        qrs,
        bruts,
        actor is None,
    )
    return bilan


def google_actif() -> bool:
    """Le bouton Google doit-il exister, ici et maintenant.

    La regle est double, et les deux branches comptent : il faut les deux cles *et* que l'exploitant
    n'ait pas pose `GOOGLE_LOGIN_ENABLED=0`. Le deuxieme volet sert au declenchement en cas de panne
    du cote Google ou de litige sur la `redirect_uri` : couper l'aclee sans redemarrer une autre
    branche de configuration.
    """
    if settings.GOOGLE_LOGIN_ENABLED is False:
        return False
    if settings.GOOGLE_LOGIN_ENABLED is True:
        return True
    if settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET:
        return True
    # ... ou configuree a l'allauth (fournisseurs declares dans les reglages, ce que fait le test
    # d'integration, et ce que produit un `SOCIALACCOUNT_PROVIDERS` pose a la main en prod).
    fournisseurs = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {}) or {}
    if (fournisseurs.get("google") or {}).get("APPS"):
        return True
    # Deuxieme facon de configurer Google : creer l'`SocialApp` depuis `django-admin`. Si l'on ne
    # regardait que les variables d'environnement, ce compte-la aurait un bouton cache alors que le
    # flux fonctionnerait — et l'exploitant conclurait a un bug du front.
    try:
        from allauth.socialaccount.models import SocialApp

        return SocialApp.objects.filter(provider="google").exists()
    except Exception:  # noqa: BLE001 - table absente (allauth socialaccount non installe) = non configure
        return False
