"""Quotas de création. Le plafond porte sur les QR **dynamiques** sur 30 jours glissants.

Les QR statiques ne coûtent rien au serveur (le navigateur les fabrique) : les plafonner punirait
l'usage gratuit sans protéger l'infrastructure — c'est le lien entre le modèle économique et la
sécurité, autant l'écrire.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.exemption import exonere_de_facturation as _exonere

WINDOW_DAYS = 30


def dynamic_usage(user) -> tuple[int, int]:
    from apps.qr.models import QrCode

    since = timezone.now() - timedelta(days=WINDOW_DAYS)
    used = QrCode.objects.filter(
        owner=user,
        kind="dynamic",
        created_at__gte=since,
        deleted_at__isnull=True,
    ).count()
    return used, user.quota_dynamic


def assert_can_create_dynamic(user) -> None:
    """Deux gardes differents, deux codes differents : ce n'est pas la meme conversation.

    * le palier ne donne pas acces au QR dynamique -> `plan_required` (402, avec le palier a vendre) ;
    * le palier donne acces mais le quota 30 jours est epuise -> `quota_exceeded` (402, avec le
      compteur). Un client qui a deja paye ne doit jamais lire « achetez Premium ».
    """
    from apps.accounts import plans
    from apps.common.exceptions import ApiError

    if _exonere(user):  # regle unique, voir apps/accounts/exemption.py
        return  # le personnel cree des QR de test sans consommer la grille
    if not plans.a_caracteristique(user.plan_effectif, "qr_dynamique"):
        requis = next(c for c in plans.ORDRE if "qr_dynamique" in plans.PALIERS[c].apporte)
        raise ApiError(
            "plan_required",
            f"Les QR dynamiques sont reserves au palier {plans.palier(requis).nom} "
            f"({plans.palier(requis).prix_eur} EUR/mois).",
            status_code=402,
            details={"caracteristique": "qr_dynamique", "palier_requis": requis, "plan_actuel": user.plan_effectif},
        )
    used, limit = dynamic_usage(user)
    if used >= limit:
        raise ApiError(
            "quota_exceeded",
            f"Limite de {limit} QR dynamiques atteinte sur {WINDOW_DAYS} jours. "
            "Passez au palier superieur ou archivez des QR inactifs.",
            status_code=402,
            details={"used": used, "limit": limit, "window_days": WINDOW_DAYS},
        )


def assert_can_create_static(user) -> None:
    """Les QR statiques ne coutent rien au serveur, mais ils coutent des octets de base et un slug :
    le plafond existe, il est juste haut. Depasse -> 402 egalement (meme contrat cote client)."""
    from apps.common.exceptions import ApiError
    from apps.qr.models import QrCode

    if _exonere(user):  # regle unique, voir apps/accounts/exemption.py
        return
    total = QrCode.objects.filter(owner=user, deleted_at__isnull=True).count()
    limite = user.quota_statique
    if total >= limite:
        raise ApiError(
            "quota_exceeded",
            f"Vous avez atteint les {limite} QR de votre palier.",
            status_code=402,
            details={"used": total, "limit": limite, "palier": user.plan_effectif},
        )


def active_qr_count(user) -> int:
    from apps.qr.models import QrCode

    return QrCode.objects.filter(owner=user, deleted_at__isnull=True).count()


def soon_to_expire(user) -> QuerySet[QrCode]:
    from apps.qr.models import QrCode

    horizon = timezone.now() + timedelta(days=7)
    return QrCode.objects.filter(owner=user, updated_at__lt=horizon, is_active=True)


if TYPE_CHECKING:  # pragma: no cover
    from apps.qr.models import QrCode


if TYPE_CHECKING:  # pragma: no cover - uniquement pour le typeur
    from apps.qr.models import QrCode
