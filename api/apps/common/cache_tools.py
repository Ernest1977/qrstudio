"""Accès cache tolérant aux pannes.

Règle produit (§10 d'ARCHITECTURE.md) : **un scan ne doit jamais échouer parce que le cache est
KO**. Le backend Redis natif de Django lève sur connexion perdue — on enveloppe donc les accès du
chemin chaud et on retient la leçon : `None` signifie "je ne sais pas", jamais "absent".
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Sentinelle distinguant « clé absente » de « valeur en cache = None ».
MISS = object()


def get_or_miss(key: str, default: Any = MISS) -> Any:
    try:
        return cache.get(key, default)
    except Exception as exc:  # noqa: BLE001 - le repli est la base de données, pas une 500
        logger.warning("cache.get en échec, repli base de données: %s: %s", type(exc).__name__, exc)
        return default


def put(key: str, value: Any, timeout: int | None = None) -> bool:
    try:
        cache.set(key, value, timeout)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache.set en échec (%s: %s)", type(exc).__name__, exc)
        return False


def delete(*keys: str) -> None:
    for key in keys:
        try:
            cache.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache.delete en échec (%s: %s)", type(exc).__name__, exc)


def bump(key: str, amount: int = 1, timeout: int | None = None) -> int | None:
    """Compteur incrémental ; `None` si le cache est injoignable (le scan compte quand même)."""
    try:
        return cache.incr(key, amount)
    except ValueError:
        try:
            cache.add(key, amount, timeout)
            return cache.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("compteur en échec (%s)", exc)
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("compteur en échec (%s)", exc)
        return None
