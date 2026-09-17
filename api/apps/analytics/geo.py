"""Géolocalisation par IP — avec sa limite assumée.

MaxMind GeoLite2 est chargé **en mémoire** (`MODE_MEMORY` est le plus lent à l'ouverture, le plus
prévisible à l'usage : pas de mmap qui disparaît quand le fichier de base est remplacé par la mise à
jour hebdomadaire, ce qui casserait le flux d'ingest de façon spectaculaire).

Aucune base, aucune clé ? `country_from_ip` renvoie `None` : on stocke le scan **sans pays** plutôt
que de le jeter. Un scan sans géo reste un scan compté ; c'est le dégradé correct.
"""

from __future__ import annotations

import logging
import threading
from ipaddress import ip_address
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_reader = None
_tried = False

RESERVED_COUNTRIES = {"A1", "A2", "XX", "--"}


def _db_path() -> Path | None:
    raw = str((settings.QR or {}).get("GEO_DB_PATH", "") or "")
    path = Path(raw) if raw else None
    return path if path and path.is_file() else None


def enabled() -> bool:
    return bool((settings.QR or {}).get("GEO_ENABLED", True)) and _db_path() is not None


def _get_reader():
    global _reader, _tried
    if _reader is not None or _tried:
        return _reader
    with _lock:
        if _reader is not None or _tried:
            return _reader
        _tried = True
        path = _db_path()
        if path is None:
            logger.info("base GeoIP absente : les scans seront stockes sans pays")
            return None
        try:
            import geoip2.database

            # `mode=AUTO` : maxminddb choisit l'extension C, puis mmap, puis la lecture fichier. On
            # n'épingle pas MODE_MEMORY : le chemin du fichier est géré par le gestionnaire de base,
            # et une constante propre à une version de la lib nous casserait au premier `pip -U`.
            _reader = geoip2.database.Reader(str(path))
        except Exception as exc:  # noqa: BLE001 - la géo n'est jamais critique
            logger.error("ouverture base GeoIP en echec (%s)", exc)
            _reader = None
    return _reader


def reset_for_tests() -> None:
    global _reader, _tried
    _reader, _tried = None, False


def country_from_ip(ip: str | None) -> dict[str, str | None]:
    """`{country_code, region, city}` — ville seulement si `GEO_CITY_ENABLED` (RGPD, opt-in)."""
    if not ip or not enabled():
        return {"country_code": None, "region": None, "city": None}
    try:
        if ip_address(ip).is_private:
            return {"country_code": None, "region": None, "city": None}
    except ValueError:
        return {"country_code": None, "region": None, "city": None}
    reader = _get_reader()
    if reader is None:
        return {"country_code": None, "region": None, "city": None}
    want_city = bool((settings.QR or {}).get("GEO_CITY_ENABLED", False))
    try:
        if want_city:
            record = reader.city(ip)
            return {
                "country_code": _country(record),
                "region": (record.subdivisions.most_specific.name or "")[:3] if record.subdivisions else None,
                "city": record.city.name if record.city and record.city.name else None,
            }
        record = reader.country(ip)
        return {"country_code": _country(record), "region": None, "city": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("geolocalisation en echec pour %s: %s", ip[:3] + "***", exc)
        return {"country_code": None, "region": None, "city": None}


def _country(record) -> str | None:
    code = getattr(getattr(record, "country", None), "iso_code", None)
    if not code or code in RESERVED_COUNTRIES:
        return None
    return code.upper()[:2]
