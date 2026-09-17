"""Ce qui doit être vrai *en production* — vérifié sur le module de réglages, pas sur ceux des tests."""

from __future__ import annotations

import importlib


def _prod(monkeypatch, tmp_path):
    # `ENV_FILE` vers un fichier inexistant : sinon le `.env` de développement du dépôt (qui porte
    # SHORT_BASE_URL=http://localhost:8000) gagne et le test jugerait la prod avec des valeurs locales.
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.setenv("DJANGO_ENV", "prod")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "x" * 64 + "-cle-de-test-unique-et-longue-0123456789")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "qrstudio.kamcofarm.com")
    monkeypatch.setenv("DJANGO_CSRF_TRUSTED_ORIGINS", "https://qrstudio.kamcofarm.com")
    monkeypatch.setenv("MAXMIND_CITY_DB", "")
    from config import env as env_module

    importlib.reload(env_module)  # relit ENV_FILE / DJANGO_ENV, puis on régénère la chaîne de réglages
    importlib.reload(importlib.import_module("config.settings.base"))
    return importlib.reload(importlib.import_module("config.settings.prod"))


def test_le_module_prod_est_durci(monkeypatch, tmp_path):
    prod = _prod(monkeypatch, tmp_path)
    assert prod.DEBUG is False
    assert prod.ALLOWED_HOSTS == ["qrstudio.kamcofarm.com"]
    assert prod.SECURE_HSTS_SECONDS >= 31_536_000
    assert prod.SECURE_SSL_REDIRECT is True
    assert prod.SESSION_COOKIE_SECURE and prod.CSRF_COOKIE_SECURE
    assert prod.SESSION_COOKIE_SAMESITE == "Lax"
    assert prod.X_FRAME_OPTIONS == "DENY"
    assert prod.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert "argon2" in prod.PASSWORD_HASHERS[0].lower()
    assert prod.SESSION_ENGINE.endswith("cached_db")  # survit à un purge de Redis
    assert prod.QR["ALLOW_PRIVATE_TARGETS"] is False  # anti-SSRF actif
    assert prod.QR["SCAN_STREAM_ENABLED"] is True


def test_le_domaine_imprime_est_le_seul_accepte(monkeypatch, tmp_path):
    """Le QR est imprimé : une faute de frappe dans `ALLOWED_HOSTS` casse le service silencieusement."""
    prod = _prod(monkeypatch, tmp_path)
    # Django attend des origines complètes (schéma inclus) dans CSRF_TRUSTED_ORIGINS.
    assert "https://qrstudio.kamcofarm.com" in prod.CSRF_TRUSTED_ORIGINS
    assert prod.QR["SHORT_BASE_URL"] == "https://qrstudio.kamcofarm.com"


def test_sans_cle_secrete_en_prod_on_refuse_de_demarrer(monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "prod")
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import pytest

    from config import env

    with pytest.raises(RuntimeError, match="DJANGO_SECRET_KEY"):
        env.get_secret("DJANGO_SECRET_KEY")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        env.get_secret("DATABASE_URL")


def test_url_de_base_invalidee_refusee(monkeypatch):
    from config import env

    with __import__("pytest").raises(ValueError, match="non supporté"):
        env.parse_db_url("mysql://u@h/db")


def test_url_postgres_complete(monkeypatch):
    from config import env

    parsed = env.parse_db_url("postgres://qrs:s%40cret@db.internal:6432/qrstudio?sslmode=require", conn_max_age=0)
    assert parsed["ENGINE"].endswith("postgresql")
    assert parsed["HOST"] == "db.internal" and parsed["PORT"] == 6432  # PgBouncer
    assert parsed["PASSWORD"] == "s@cret"  # le %40 est bien décodé
    assert parsed["OPTIONS"] == {"sslmode": "require"}
    assert parsed["CONN_MAX_AGE"] == 0, "le pooling est dans PgBouncer, pas dans Django"


def test_admin_nest_pas_sur_le_chemin_par_defaut(monkeypatch, tmp_path):
    prod = _prod(monkeypatch, tmp_path)
    assert prod.ADMIN_URL != "admin/"
    assert prod.ADMIN_URL.endswith("/")


def test_le_cache_est_traverse_de_facon_tolerante(monkeypatch):
    """Une panne de cache doit être bruyante dans les logs et silencieuse pour l'utilisateur."""

    from apps.common import cache_tools

    class HS:
        def get(self, key, default=None):
            raise ConnectionError("redis down")

        def set(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr("apps.common.cache_tools.cache", HS())
    with self_assert_no_raise():
        assert cache_tools.get_or_miss("cle") is cache_tools.MISS
        assert cache_tools.put("cle", 1) is False


class self_assert_no_raise:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_le_compteur_survit_a_uneCle_absente():
    from django.core.cache import cache

    cache.delete("qrs:test:bump")
    assert cache_tools_bump("qrs:test:bump") == 1
    assert cache_tools_bump("qrs:test:bump") == 2


def cache_tools_bump(cle):
    from apps.common import cache_tools

    return cache_tools.bump(cle, timeout=30)
