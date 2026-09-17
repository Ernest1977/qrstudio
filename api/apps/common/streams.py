"""File d'ingestion des scans (Redis Stream) + repli.

Le chemin chaud fait un `XADD` et repart ; l'écriture SQL se fait en lot côté consommateur
(sprint 3). Deux règles non négociables, toutes deux testées :

1. **un échec d'ingest ne doit jamais empêcher la redirection** — on avale, on loggue, on compte
   les pertes ;
2. le flux est borné (`maxlen` approx) pour qu'un pic ne mange pas toute la mémoire de Redis.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from apps.common import redis_client

logger = logging.getLogger(__name__)


def _qr(key: str, default: Any) -> Any:
    return (getattr(settings, "QR", {}) or {}).get(key, default)


def is_enabled() -> bool:
    return bool(_qr("SCAN_STREAM_ENABLED", False)) and redis_client.redis_available()


async def push_scan(event: dict[str, Any]) -> bool:
    """Publie un scan. Retourne `False` en cas d'échec (le 302 est déjà parti, le scan reste compté)."""
    if not is_enabled():
        return False
    client = redis_client.get_redis_async()
    if client is None:
        return False
    stream = _qr("SCAN_STREAM", "stream:scans")
    maxlen = int(_qr("SCAN_STREAM_MAXLEN", 500_000))
    try:
        await client.xadd(stream, _flatten(event), maxlen=maxlen, approximate=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("xadd refusé (%s: %s) — scan perdu pour les stats, redirection OK", type(exc).__name__, exc)
        return False
    return True


def push_scan_sync(event: dict[str, Any]) -> bool:
    """Variante synchrone (tâches Celery, tests, scripts d'admin)."""
    if not is_enabled():
        return False
    client = redis_client.get_redis()
    if client is None:
        return False
    try:
        client.xadd(
            _qr("SCAN_STREAM", "stream:scans"),
            _flatten(event),
            maxlen=int(_qr("SCAN_STREAM_MAXLEN", 500_000)),
            approximate=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("xadd sync refusé (%s)", exc)
        return False
    return True


def stream_length() -> int:
    client = redis_client.get_redis()
    if client is None:
        return 0
    try:
        return int(client.xlen(_qr("SCAN_STREAM", "stream:scans")))
    except Exception:  # noqa: BLE001
        return 0


def _flatten(event: dict[str, Any]) -> dict[str, str]:
    """Redis Streams n'accepte que des scalaires ; les valeurs `None` deviennent `""` (champ vide)."""
    flat: dict[str, str] = {}
    for key, value in event.items():
        if value is None:
            flat[key] = ""
        elif isinstance(value, bool):
            flat[key] = "1" if value else "0"
        else:
            flat[key] = str(value)
    return flat
