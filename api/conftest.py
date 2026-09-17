"""Fixtures partagées. `pytest-django` lit `DJANGO_SETTINGS_MODULE` depuis `pyproject.toml`."""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")
import shutil
import tempfile
from pathlib import Path

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """Un throttle ou une clé de redirect qui traîne d'un test à l'autre est un faux négatif garanti."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def tmp_storage(monkeypatch, tmp_path: Path):
    """Répertoires d'export isolés par test (sinon `exports/` se remplit à chaque exécution)."""
    monkeypatch.setattr("django.conf.settings.EXPORT_ROOT", tmp_path / "exports", raising=False)
    return tmp_path


@pytest.fixture
def run_async():
    """Pilote une vue asynchrone sans serveur ASGI.

    Choix délibéré pour les tests du chemin chaud : `Client` est synchrone et ne sait pas appeler une
    vue `async def` ; monter un vrai serveur pour trois assertions en ajouterait plus qu'il n'en retire.
    """

    def _run(coroutine):
        return asyncio.run(coroutine)

    return _run


@pytest.fixture
def user(django_user_model):
    """Compte **premium** par défaut.

    Ce n'est pas un raccourci : la grande majorité des tests de ce fichier parlent du CRUD d'un QR
    dynamique et des statistiques, qui sont des fonctions payantes depuis la grille réelle. Laisser le
    fixture en `free` transformerait chaque test de lecture en test de facturation — et, pire, un
    402 lu comme un 403 pourrait masquer une vraie régression d'accès. Les paliers ont leur propre
    fichier (`tests/test_plans.py`), avec leurs propres comptes.
    """
    return django_user_model.objects.create_user(
        email="Marie@Exemple.COM",
        password="un-mot-de-passe-solide-42",
        plan="premium",
    )


@pytest.fixture
def consented_user(django_user_model):
    from django.utils import timezone

    return django_user_model.objects.create_user(
        email="pro@kamcofarm.com",
        password="un-mot-de-passe-solide-42",
        plan="premium",
        consent_tracking_at=timezone.now(),
    )


@pytest.fixture
def other_user(django_user_model):
    return django_user_model.objects.create_user(email="autre@exemple.com", password="encore-un-bon-mot-de-passe")


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def auth_api(api, user):
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def dynamic_qr(user):
    from apps.qr.models import QrCode

    return QrCode.objects.create(
        owner=user,
        kind="dynamic",
        type_id="url",
        label="Affiche boutique",
        target_url="https://kamcofarm.com/boutique",
    )
