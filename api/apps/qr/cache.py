"""Cache du chemin chaud, en JSON dans Redis — et non via l'API `cache` de Django.

Pourquoi court-circuiter le backend de cache : `RedisCache` sérialise (pickle) et préfixe les clés.
La vue de redirect tourne en `async` avec son propre client Redis : partager la clé de Django
signifierait dépendre de son format interne. On écrit donc **notre** payload JSON, avec nos propres
clés `qrs:redirect:<slug>`, lisibles à la main en incident (`redis-cli get`).

Le repli `LocMemCache` (dev, tests) garde la même sémantique : valeur dict, TTL court, et une entrée
négative pour que le scan de slugs aléatoires ne parte pas en base à chaque requête.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings

from apps.common import redis_client

logger = logging.getLogger(__name__)

TTL = 60
NEGATIVE_TTL = 10
MISS: dict[str, Any] = {"missing": True}


def _ttl() -> int:
    return int((settings.QR or {}).get("REDIRECT_CACHE_TTL", TTL))


def key_for(slug: str) -> str:
    return f"qrs:redirect:{slug}"


# ------------------------------------------------------------------ synchrone
def read(slug: str) -> dict[str, Any] | None:
    client = redis_client.get_redis()
    if client is None:
        return _fallback_get(slug)
    try:
        raw = client.get(key_for(slug))
    except Exception as exc:  # noqa: BLE001
        logger.warning("lecture cache redirect en echec (%s) : repli base", exc)
        return None
    return _loads(raw)


def write(slug: str, payload: dict[str, Any], *, ttl: int | None = None) -> None:
    client = redis_client.get_redis()
    if client is None:
        _fallback_set(slug, payload, ttl)
        return
    try:
        client.set(key_for(slug), json.dumps(payload), ex=ttl or (_ttl() if payload is not MISS else NEGATIVE_TTL))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ecriture cache redirect en echec (%s)", exc)


def invalidate(slug: str) -> None:
    if not slug:
        return
    client = redis_client.get_redis()
    if client is None:
        _fallback_delete(slug)
        return
    try:
        client.delete(key_for(slug))
    except Exception as exc:  # noqa: BLE001
        logger.warning("invalidation cache en echec (%s) : le TTL de %ss fera converger", exc, _ttl())


def invalidate_many(slugs) -> None:
    for slug in slugs:
        invalidate(slug)


# ------------------------------------------------------------------ asynchrone
async def read_async(slug: str) -> dict[str, Any] | None:
    client = redis_client.get_redis_async()
    if client is None:
        from asgiref.sync import sync_to_async

        return await sync_to_async(_fallback_get)(slug)
    try:
        raw = await client.get(key_for(slug))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache async en echec (%s) : repli base de donnees", exc)
        return None
    return _loads(raw)


async def write_async(slug: str, payload: dict[str, Any], *, ttl: int | None = None) -> None:
    client = redis_client.get_redis_async()
    if client is None:
        from asgiref.sync import sync_to_async

        await sync_to_async(_fallback_set)(slug, payload, ttl)
        return
    try:
        await client.set(
            key_for(slug),
            json.dumps(payload),
            ex=ttl or (_ttl() if payload is not MISS else NEGATIVE_TTL),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache async set en echec (%s)", exc)


# ------------------------------------------------------------------ helpers
def _loads(raw) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _fallback_get(slug: str):
    from django.core.cache import cache

    value = cache.get(key_for(slug))
    return value if isinstance(value, dict) else None


def _fallback_set(slug: str, payload, ttl) -> None:
    from django.core.cache import cache

    cache.set(key_for(slug), payload, ttl or (_ttl() if payload is not MISS else NEGATIVE_TTL))


def _fallback_delete(slug: str) -> None:
    from django.core.cache import cache

    cache.delete(key_for(slug))


def serialize_for_cache(qr) -> dict[str, Any]:
    """Ce que la vue de redirect sait faire sans la base. Une clé = une décision."""
    return {
        "qr_id": qr.pk,
        "target_url": qr.target_url or "",
        "redirect_mode": qr.redirect_mode,
        "is_active": bool(qr.is_active and qr.deleted_at is None),
        "kind": qr.kind,
        "owner_id": qr.owner_id,
        "consent": bool(qr.owner.can_track) if qr.owner_id else False,
        "utm": bool(qr.utm_mode),
        # La validite d'une offre est lue ici, pas en base : la redirection ne doit jamais attendre un
        # JOIN. Le compteur d'usages, lui, est dans Redis (`promo.marquer_utilise`) — la valeur ci-dessous
        # est la baseline resseree par `sync_promo_usages`.
        # Une campagne = un ou plusieurs codes sur le MEME QR (50 flyers, 50 budgets). Les stocker dans
        # l'entree coute ~120 o par code et evite au chemin chaud une requete par scan : la redirection
        # doit pouvoir juger le code presente par le visiteur sans toucher la base.
        **_promo_de(qr),
    }


def _promo_de(qr) -> dict[str, Any]:
    """L'offre (ou la campagne de codes) portee par ce QR, sous la forme que la redirection sait juger.

    Deux cles, deux usages : `promo` est l'offre attachee au flyer (« une offre, une impression ») et
    `codes_promo` est la table code -> offre pour le visiteur qui **presente** un code. Les deux sont des
    structures plates en ASCII, decodees une fois et lues des millions de fois.
    """
    lignes = getattr(qr, "_promo_lues", None)
    if lignes is None:
        from apps.qr.models import PromoCode

        lignes = list(
            PromoCode.objects.filter(qr_id=qr.pk, actif=True)
            .order_by("-expire_le", "code")
            .values(
                "pk", "code", "expire_le", "usages", "usages_max", "remise_type", "remise_valeur", "devise", "libelle"
            )
        )
    if not lignes:
        return {"promo": None, "codes_promo": {}}
    formes = {ligne["code"]: _forme_promo(ligne) for ligne in lignes}
    return {"promo": formes[lignes[0]["code"]], "codes_promo": formes}


def _forme_promo(promo: dict) -> dict[str, Any]:
    expire_le = promo["expire_le"]
    return {
        "id": promo["pk"],
        "code": promo["code"],
        "libelle": promo["libelle"] or "",
        "expire_le": expire_le.isoformat() if expire_le else "",
        # La comparaison de validite se fait sur cet epoch, jamais sur la chaine ISO : un fuseau qui se
        # glisse dans la serie casserait un tri lexicale en silence.
        "expire_ts": int(expire_le.timestamp()) if expire_le else 0,
        "usages": int(promo["usages"] or 0),
        "usages_max": int(promo["usages_max"]) if promo["usages_max"] else None,
        "remise_type": promo["remise_type"],
        "remise_valeur": str(promo["remise_valeur"]) if promo["remise_valeur"] is not None else None,
        "devise": promo["devise"] or "",
    }


def negative_entry() -> dict[str, Any]:
    return dict(MISS)
