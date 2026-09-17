import pytest
from django.urls import reverse  # noqa: F401  (garde-fou : les URLs testées sont bien résolues par le routeur)

pytestmark = pytest.mark.django_db


def test_healthz_vert_quand_la_base_repond(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # Sonde sans détail : pas de nom de table, pas de version, pas de liste d'apps.
    assert set(body) <= {"status", "cache"}


def test_healthz_degrade_quand_la_base_est_hors_ligne(client, monkeypatch):
    class Broken:
        def cursor(self):
            raise RuntimeError("base morte")

    monkeypatch.setattr("django.db.connection", Broken())
    response = client.get("/healthz/")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_schema_openapi_se_genere(client):
    response = client.get("/api/v1/schema/")
    assert response.status_code == 200, response.content[:400]
    assert b"/api/v1/qr/" in response.content
    assert b"quota_exceeded" in response.content or b"target_url" in response.content


def test_documentation_swagger(client):
    assert client.get("/api/v1/docs/").status_code == 200
