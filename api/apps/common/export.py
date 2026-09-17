"""Exports CSV asynchrones, avec lien signé et jeton à usage unique.

Pourquoi du côté serveur alors que le front a déjà les données : un export de 500 000 lignes ne tient
pas dans un onglet. Le jeton aléatoire vit 15 minutes dans le cache ; le fichier est écrit dans
`EXPORT_ROOT` puis supprimé après téléchargement (et par la tâche de nettoyage).
"""

from __future__ import annotations

import csv
import io
import logging
import secrets
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_TTL = 60 * 15


def _root() -> Path:
    root = Path(getattr(settings, "EXPORT_ROOT", settings.BASE_DIR / "exports"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _key(token: str) -> str:
    return f"qrs:export:{token}"


def enqueue_csv_export(*, model_label: str, queryset_pks: list[int], actor) -> str:
    """Enregistre la demande et la passe à Celery (en direct si `CELERY_TASK_ALWAYS_EAGER`)."""
    token = secrets.token_urlsafe(16)
    cache.set(
        _key(token),
        {"model": model_label, "pks": queryset_pks[:500_000], "actor_id": getattr(actor, "pk", None), "done": False},
        TOKEN_TTL,
    )
    from apps.common.tasks import run_csv_export

    run_csv_export.delay(token)
    return token


def build_csv(model_label: str, pks: list[int]) -> str:
    from django.apps import apps as django_apps
    from django.db import models

    try:
        app_label, _, model_name = model_label.rpartition(".")
        model = django_apps.get_model(app_label, model_name)
    except LookupError:
        return ""
    fields = [
        f.name
        for f in model._meta.get_fields()
        if (isinstance(f, (models.ForeignKey, models.OneToOneField)) and f.concrete)
        or (getattr(f, "concrete", False) and not getattr(f, "is_relation", False))
    ]
    rows = model._default_manager.filter(pk__in=pks).values_list(*fields)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(fields)
    for row in rows.iterator(chunk_size=5000):
        writer.writerow(["" if value is None else value for value in row])
    return out.getvalue()


def consume_token(token: str) -> dict | None:
    """Récupère et **consomme** le jeton : un lien d'export ne doit pas être téléchargeable deux fois."""
    data = cache.get(_key(token))
    if data is None:
        return None
    cache.delete(_key(token))
    return data


def write_export_file(token: str, content: str) -> Path:
    path = _root() / f"export-{token}.csv"
    path.write_text(content, encoding="utf-8")
    return path


def read_export_file(token: str) -> Path | None:
    path = _root() / f"export-{token}.csv"
    if path.is_file():
        return path
    logger.warning("fichier d'export manquant token=%s", token[:6])
    return None
