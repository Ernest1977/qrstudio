"""Clients Redis (sync et async) partagés.

Le chemin chaud (`/r/<slug>`) est une vue asynchrone : il lui faut `redis.asyncio`. Le reste du
projet (tâches Celery, invalidations, commands) utilise le client synchrone. Les deux sont paresseux
et optionnels : **absent de Redis = l'application tourne**, juste plus lentement (repli cache→base).
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_sync_client: Any | None = None
_sync_failed = False
_async_clients: dict[int, Any] = {}


def _url() -> str:
    return getattr(settings, "REDIS_URL", "") or ""


def redis_available() -> bool:
    return bool(_url())


def get_redis() -> Any | None:
    """Client sync, ou `None` si Redis n'est pas configuré (les appelsants ont un repli)."""
    global _sync_client, _sync_failed
    if _sync_client is not None or _sync_failed:
        return _sync_client
    url = _url()
    if not url:
        return None
    try:
        import redis

        _sync_client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            health_check_interval=30,
        )
    except Exception as exc:  # noqa: BLE001
        _sync_failed = True
        logger.error("redis indisponible, mode repli actif: %s", exc)
        return None
    return _sync_client


def get_redis_async() -> Any | None:
    """Client async lié à la boucle de l'événement courant (un client par loop, sinon `RuntimeError`)."""
    import asyncio

    if not redis_available():
        return None
    loop_key = id(asyncio.get_running_loop())
    client = _async_clients.get(loop_key)
    if client is not None:
        return client
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            _url(),
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            max_connections=200,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("client redis async refusé (%s) : ingest désactivé", exc)
        return None
    _async_clients[loop_key] = client
    return client


def cache_key(*parts: str) -> str:
    """Clé lisible : `qrs:redirect:<slug>` — utile pour `redis-cli --scan` en incident."""
    return ":".join(("qrs", *parts))
