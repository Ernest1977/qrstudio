"""Lecture de la configuration : un seul endroit touche `os.environ`.

On évite une dépendance (django-environ, pydantic-settings) pour 40 lignes de logique :
moins de surface d'audit, et `manage.py check --deploy` reste l'arbitre des réglages sécurité.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Charge un `.env` simple dans os.environ (sans écraser ce qui est déjà posé)."""
    env_file = path or BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "vrai", "oui"}


def get_int(name: str, default: int) -> int:
    raw = get(name)
    try:
        return int(raw)
    except ValueError:
        return default


def get_list(name: str, default: tuple[str, ...] = ()) -> list[str]:
    raw = get(name)
    return [item.strip() for item in raw.split(",") if item.strip()] or list(default)


def get_secret(name: str, default: str = "") -> str:
    """Clé secrète : en prod l'absence est une erreur, jamais un `default` silencieux."""
    value = get(name, default)
    if not value and get("DJANGO_ENV", "dev") == "prod":
        raise RuntimeError(f"{name} est requis en production (voir .env.example)")
    return value


def parse_db_url(url: str, *, conn_max_age: int = 600) -> dict:
    """`postgres://user:pass@host:5432/db?sslmode=require` → dict DATABASES."""
    parts = urlsplit(url)
    engine = "django.db.backends.postgresql"
    if parts.scheme.startswith(("sqlite", "postgres")) is False:
        raise ValueError(f"schéma d'URL de base non supporté : {parts.scheme!r}")
    if parts.scheme.startswith("sqlite"):
        path = unquote(parts.path or parts.netloc)
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": path or str(BASE_DIR / "db.sqlite3"),
            "CONN_MAX_AGE": 0,
        }
    options: dict[str, str] = {}
    for pair in (parts.query or "").split("&"):
        if not pair or "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        options[key] = value
    return {
        "ENGINE": engine,
        "NAME": unquote(parts.path.lstrip("/")),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "localhost",
        "PORT": parts.port or 5432,
        "CONN_MAX_AGE": conn_max_age,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
        "ATOMIC_REQUESTS": False,
    }
