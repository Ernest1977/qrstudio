"""Accès du personnel : 2FA (TOTP) exigée pour `django-admin`, audit des impersonnalisations.

`allauth.mfa` stocke les authentificateurs ; on interroge le modèle **sans l'importer en dur** pour
que l'application démarre même si un déploiement se passe d'MFA (le contrôle devient alors un refus
franc si `ADMIN_REQUIRE_MFA` est vrai, jamais un passe-droit silencieux).
"""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def has_totp(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    try:
        from allauth.mfa.models import Authenticator
    except ImportError:  # pragma: no cover - dépendance présente dans requirements.txt
        logger.warning("allauth.mfa absent : contrôle 2FA indisponible")
        return False
    totp_type = getattr(getattr(Authenticator, "Type", None), "TOTP", "totp")
    return Authenticator.objects.filter(user=user, type=totp_type).exists()


def staff_mfa_required(request) -> bool:
    """Vrai si la requête vise l'admin et que son auteur est personnel sans second facteur."""
    if not getattr(settings, "ADMIN_REQUIRE_MFA", True):
        return False
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_staff):
        return False
    return not has_totp(user)
