"""Limitation de débit par IP, sur le cache (Redis en prod) — jamais sur la base.

Deux écarts vis-à-vis de DRF, et ce sont des corrections de sécurité, pas des goûts :

* la clé est l'**IP même pour un utilisateur connecté**. DRF clé par `user.pk` dès qu'une session
  existe : un attaquant qui devine un mot de passe valide une fois sur mille se retrouve alors un
  compteur *par compte visé*, et le plafond ne protège plus du bourrage d'identifiants ;
* on ne relit jamais `X-Forwarded-For` : `BaseThrottle.get_ident` le fait, ce qui permet de forger
  son IP et de multiplier les compteurs. `request.client_ip` est posé par `ClientIPMiddleware`, la
  confiance accordée au proxy reste un seul réglage (`TRUST_PROXY_HEADERS`).

La classe est posée dans `DEFAULT_THROTTLE_CLASSES` : sans `throttle_scope` sur la vue, DRF autorise
et ne consomme rien — le surcoût est nul sur les vues non visées.
"""

from __future__ import annotations

from typing import ClassVar

from rest_framework.throttling import ScopedRateThrottle


def ident_for(request) -> str:
    return getattr(request, "client_ip", None) or request.META.get("REMOTE_ADDR", "inconnue")


class IpScopedRateThrottle(ScopedRateThrottle):
    """Même mécanique que `ScopedRateThrottle` (taux lu par scope), mais clé par IP.

    Écart avec DRF, et il est de sûreté : `ScopedRateThrottle` ne s'active **que** si la vue porte
    `throttle_scope`. Oublier l'attribut ne provoque donc aucune erreur — le compteur n'existe pas et
    la route est simplement non limitée, en silence. Ici la classe connaît déjà son taux
    (`throttle_scope` de classe), donc l'oubli côté vue retombe sur le plafond prévu. Les vues qui
    déclarent quand même leur portée gardent la main (elles peuvent viser un autre scope).
    """

    # `ClassVar[str | None]` : les sous-classes redefussent cette portee avec une chaine, et sans
    # annotation explicite le typeur lit `None` comme type definitive de l'attribut.
    throttle_scope: ClassVar[str | None] = None

    def allow_request(self, request, view):
        if getattr(view, self.scope_attr, None) is None and self.throttle_scope:
            self.scope = self.throttle_scope
            self.rate = self.get_rate()
            self.num_requests, self.duration = self.parse_rate(self.rate)
            from rest_framework.throttling import SimpleRateThrottle

            return SimpleRateThrottle.allow_request(self, request, view)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        scope = getattr(view, self.scope_attr, None) or self.throttle_scope
        if not scope:
            return None
        self.scope = scope
        return self.cache_format % {"scope": scope, "ident": ident_for(request)}


class LoginThrottle(IpScopedRateThrottle):
    throttle_scope = "login"


class RegisterThrottle(IpScopedRateThrottle):
    throttle_scope = "register"


class PasswordResetThrottle(IpScopedRateThrottle):
    throttle_scope = "password_reset"


class VerifyThrottle(IpScopedRateThrottle):
    throttle_scope = "verify"


class QrWriteThrottle(IpScopedRateThrottle):
    throttle_scope = "qr_write"


class StatsThrottle(IpScopedRateThrottle):
    throttle_scope = "stats"


class QrArtThrottle(IpScopedRateThrottle):
    """Le rendu *vérifié* (art, GIF) : un compose **plus** un décodage par absence de cache, ~10× le PNG.

    Un `throttle_scope` distinct de `qr_image` est ce qui permet de le brider sans toucher aux exports
    PNG — sinon la seule facon de tenir les workers serait de bridder tout le rendu, y compris le gratuit.
    """

    throttle_scope = "qr_art"


class QrImageThrottle(IpScopedRateThrottle):
    """Le seul endpoint qui fabrique du CPU par requête (rendu PNG/SVG) — plafonné à part."""

    throttle_scope = "qr_image"


def hit_rate(scope: str, ident: str, *, limit: int, window_seconds: int) -> tuple[int, int]:
    """Compteur de fenêtre fixe pour les vues hors DRF (le redirect).

    Retourne `(nombre_constaté, secondes_avant_la_respiration)`. Écrit dans le cache : à 700 req/s,
    compter les abus en base coûterait plus cher que le scan lui-même. Si le cache est KO, on
    renvoie `(0, window)` : on ne bloque **jamais** un scan légitime à cause d'une panne de Redis.
    """
    import time as _time

    from django.core.cache import caches

    now = int(_time.time())
    bucket = now // window_seconds
    key = f"qrs:rate:{scope}:{bucket}:{ident}"
    cache = caches["default"]
    try:
        count = cache.incr(key)
    except ValueError:
        cache.add(key, 1, window_seconds * 2)
        count = 1
    except Exception:  # noqa: BLE001
        return 0, window_seconds
    return int(count), max(1, window_seconds - (now % window_seconds))
