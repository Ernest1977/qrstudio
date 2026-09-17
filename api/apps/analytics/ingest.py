"""Construction de l'événement de scan, puis ingestion en lot depuis le flux Redis.

Deux propriétés comptent ici, pas la vitesse brute :

* **idempotence** : la clé `(scan_id, ts)` est unique, et l'insertion ignore les doublons. Un
  consommateur qui redémarre au milieu d'un lot rejoue le lot — sans ça, on ment sur les chiffres
  et le produit ne vaut plus rien.
* **troncature avant persistance** : l'IP n'est jamais stockée en clair ; on écrit le réseau /24
  (v4) ou /48 (v6), purgé après `IP_RETENTION_HOURS`. L'empreinte d'`User-Agent` est un SHA-256
  tronqué, pas la chaîne : un UA contient des identifiants matériels.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone as dj_timezone

from apps.analytics import geo
from apps.common import ip as ip_lib

logger = logging.getLogger(__name__)

BOT_MARKERS = (
    "bot",
    "crawl",
    "spider",
    "slurp",
    "curl",
    "wget",
    "python-requests",
    "headlesschrome",
    "scanner",
    "monitor",
    "check_http",
    "facebookexternalhit",
    "whatsapp",
    "telegrambot",
    "linkedinbot",
    "slackbot",
)
MOBILE_MARKERS = ("android", "iphone", "ipod", "windows phone", "mobile safari")
TABLET_MARKERS = ("ipad", "tablet", "playbook", "silk")


def build_event(
    request, *, qr_id: int | None, owner_id: int | None, consent: bool, status: int = 302
) -> dict[str, Any]:
    """Ce que la vue de pushait dans le flux. Rien de plus : pas d'UA brut, pas d'IP complète."""
    ip = getattr(request, "client_ip", None)
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:512]
    referer = request.META.get("HTTP_REFERER", "")
    truncated, prefix_len = ip_lib.truncate_ip(ip)
    now = dj_timezone.now()
    return {
        "scan_id": uuid.uuid4().hex[:32],
        "ts": now.isoformat(),
        "day": now.date().isoformat(),
        "qr_id": qr_id,
        "owner_id": owner_id,
        "ip_trunc": truncated,
        "ip_prefix_len": prefix_len,
        "user_agent_hash": _hash(user_agent),
        "referer_domain": _referer_domain(referer),
        "device_class": classify_device(user_agent),
        "is_bot": is_bot(user_agent),
        "status": int(status),
        "source": "stream",
        # La géo ne se fait **que** si le propriétaire du QR a un consentement valide : c'est le
        # point où la promesse RGPD est tenue (ou pas) — le reste du pipeline ne fait que stocker.
        "geo": country_from_request(ip) if consent else None,
    }


def country_from_request(ip: str | None) -> dict[str, str | None] | None:
    if not geo.enabled():
        return None
    return geo.country_from_ip(ip)


def classify_device(user_agent: str) -> str:
    lowered = (user_agent or "").lower()
    if any(marker in lowered for marker in MOBILE_MARKERS):
        return "mobile"
    if any(marker in lowered for marker in TABLET_MARKERS):
        return "tablet"
    if lowered:
        return "desktop"
    return "unknown"


def is_bot(user_agent: str) -> bool:
    lowered = (user_agent or "").lower()
    return any(marker in lowered for marker in BOT_MARKERS)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:32] if value else ""


def _referer_domain(referer: str) -> str:
    if not referer:
        return ""
    from urllib.parse import urlsplit

    host = urlsplit(referer).hostname or ""
    return host.lower()[:190]


def to_row(event: dict[str, Any]) -> dict[str, Any]:
    """Événement du flux (cordes) → ligne SQL typée. Les deux sens sont couverts par les tests."""
    ts = event.get("ts")
    if isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            parsed = dj_timezone.now()
        ts = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    geo_data = event.get("geo") or {}
    if isinstance(geo_data, str):  # aplatissement Redis Streams : tout est chaîne
        try:
            import json

            geo_data = json.loads(geo_data)
        except ValueError:
            geo_data = {}
    return {
        "ts": ts,
        "day": (ts.date() if ts else dj_timezone.now().date()),
        "qr_id": int(event.get("qr_id") or 0),
        "owner_id": int(event["owner_id"]) if event.get("owner_id") not in (None, "") else None,
        "country_code": (geo_data or {}).get("country_code") or None,
        "region": (geo_data or {}).get("region") or None,
        "city": (geo_data or {}).get("city") or None,
        "ip_trunc": event.get("ip_trunc") or None,
        "ip_prefix_len": int(event["ip_prefix_len"]) if event.get("ip_prefix_len") not in (None, "") else None,
        "user_agent_hash": event.get("user_agent_hash") or "",
        "referer_domain": event.get("referer_domain") or "",
        "device_class": event.get("device_class") or "unknown",
        "is_bot": str(event.get("is_bot", "0")) in {"1", "True", "true"},
        "status": int(event.get("status") or 302),
        "scan_id": event.get("scan_id") or uuid.uuid4().hex[:32],
        "source": event.get("source") or "stream",
    }


def persist_many(events: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Insertion par lot, idempotente. Retourne (insérés, doublons ignorés).

    `ignore_conflicts=True` couvre le rejouement du flux ; le `bulk_size` borné évite un unique
    `INSERT` de 5 000 lignes qui tiendrait une transaction trop longue devant les requêtes du site.
    """
    from apps.analytics.models import ScanEvent

    rows = [to_row(event) for event in events]
    if not rows:
        return 0, 0
    with transaction.atomic():
        created = ScanEvent.objects.bulk_create(
            [ScanEvent(**row) for row in rows],
            ignore_conflicts=True,
            batch_size=500,
        )
        # `ignore_conflicts` ne dit pas combien ont sauté : on le déduit (idempotence vérifiable).
        return len(created), max(0, len(rows) - len(created))


def consume_stream(*, count: int = 1000, block_ms: int = 0) -> tuple[int, int]:
    """Lit le flux Redis et l'écrit en base. Appelée par la tâche Celery `ingest_scans`.

    Groupes/consommateurs : `xgroup_create(mkstream=True)` puis `xreadgroup` + `xack` seulement après
    écriture — une panne en cours de lot rejoue le lot, et l'unicité `(scan_id, ts)` absorbe.
    """
    from apps.common import redis_client, streams

    if not streams.is_enabled():
        return 0, 0
    client = redis_client.get_redis()
    if client is None:
        return 0, 0
    stream = (settings.QR or {}).get("SCAN_STREAM", "stream:scans")
    group = "ingest"
    try:
        client.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as exc:  # noqa: BLE001 - "BUSYGROUP" = le groupe existe déjà, c'est normal
        if "BUSYGROUP" not in str(exc):
            logger.warning("xgroup_create en echec: %s", exc)
    ids: list[str] = []
    events: list[dict[str, Any]] = []
    try:
        for _name, entries in client.xreadgroup(group, "worker-1", {stream: ">"}, count=count, block=block_ms or None):
            for entry_id, fields in entries:
                ids.append(entry_id)
                events.append(dict(fields))
    except Exception as exc:  # noqa: BLE001
        logger.error("xreadgroup en echec: %s", exc)
        return 0, 0
    inserted, skipped = persist_many(events)
    if ids:
        try:
            client.xack(stream, group, *ids)
        except Exception as exc:  # noqa: BLE001 - non acquittés = rejoués, et le rejouement est sûr
            logger.warning("xack en echec (%s) : le lot sera rejoue", exc)
    return inserted, skipped
