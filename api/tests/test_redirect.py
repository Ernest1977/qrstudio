"""Le chemin chaud : 302, en-têtes, cache, dégradation, abuse. Ce sont les tests qui paient le serveur."""

import asyncio

import pytest
from django.test import RequestFactory

# `transaction=True` : sous SQLite, la vue asynchrone exécute sa lecture dans le thread sérialisé
# d'asgiref, donc sur une *autre* connexion que celle du test — le test reste inapte s'il est lui-même
# empaqueté dans la transaction rollback de `TestCase` (« table is locked »). Sur PostgreSQL (CI, prod)
# ce choix est neutre : on écrit le test pour les deux.
pytestmark = pytest.mark.django_db(transaction=True)


def get(
    slug,
    *,
    method="get",
    query="",
    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
    ip="203.0.113.7",
    referer="",
):
    factory = RequestFactory()
    path = f"/r/{slug}{query}"
    request = getattr(factory, method)(path, HTTP_USER_AGENT=user_agent, HTTP_REFERER=referer, REMOTE_ADDR=ip)
    request.client_ip = ip
    from apps.redirect.views import scan_redirect

    return asyncio.run(scan_redirect(request, slug))


def test_scan_redirige_et_ne_se_met_nulle_part_en_cache(dynamic_qr):
    response = get(dynamic_qr.slug)
    assert response.status_code == 302
    assert response["Location"] == "https://kamcofarm.com/boutique"
    assert response["Cache-Control"] == "no-store, max-age=0"
    assert response["X-Robots-Tag"].startswith("noindex")
    assert response["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response["X-Redirect-Cache"] == "miss"


def test_le_deuxieme_scan_ne_touche_pas_la_base(dynamic_qr):
    """Le cache est le seul motif pour lequel 3 machines suffisent à 700 req/s : on le vérifie."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    get(dynamic_qr.slug)
    with CaptureQueriesContext(connection) as captures:
        response = get(dynamic_qr.slug)
    assert response.status_code == 302
    assert response["X-Redirect-Cache"] == "hit"
    assert len(captures.captured_queries) == 0, captures.captured_queries


def test_slug_inconnu_410_puis_cache_negatif(dynamic_qr):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    reponse = get("zzzzzzzz")
    assert reponse.status_code == 410
    corps = reponse.content.decode("utf-8")
    assert "n’affiche plus de contenu" in corps
    with CaptureQueriesContext(connection) as captures:
        get("zzzzzzzz")
    assert len(captures.captured_queries) == 0, "un slug inconnu ne doit pas repartir en base à chaque visite"


@pytest.mark.parametrize("slug", ["", "a", "0O1I-l", "x" * 40, "AB-CD", "a_b_c"])
def test_slugs_mal_formes_meurent_avant_la_base(slug, dynamic_qr):
    """Aucun de ces cas ne doit produire une requête SQL : c'est la porte d'entrée du trafic de bot."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captures:
        response = get(slug)
    assert response.status_code == 404, f"slug {slug!r} accepté à tort"
    assert len(captures.captured_queries) == 0


@pytest.mark.parametrize("slug", ["abcd", "SELECT", "zzzzzzzz"])
def test_slug_valide_mais_inconnu_rend_410(slug, dynamic_qr):
    """Bien formé mais absent : 410 « parti », pas 404 « jamais existé » — le visiteur ne relit pas un lien mort."""
    response = get(slug)
    assert response.status_code == 410


def test_qr_en_pause_rend_une_page_lisible_et_non_un_404(dynamic_qr):
    from apps.qr import cache as qr_cache

    qr_cache.invalidate(dynamic_qr.slug)
    dynamic_qr.pause()
    response = get(dynamic_qr.slug)
    assert response.status_code == 410
    assert "pause" in response.content.decode("utf-8")


def test_head_ne_compte_pas_de_scan(dynamic_qr, monkeypatch):
    from apps.common import streams

    appeles = []
    monkeypatch.setattr(streams, "push_scan", lambda event: appeles.append(event) or asyncio.sleep(0))
    response = get(dynamic_qr.slug, method="head")
    assert response.status_code == 200
    assert appeles == []


def test_sonde_de_monitoring_ne_compte_pas(dynamic_qr, monkeypatch):
    from apps.common import streams

    appeles = []
    monkeypatch.setattr(streams, "push_scan", lambda event: appeles.append(event) or asyncio.sleep(0))
    get(dynamic_qr.slug, query="?_probe=1")
    assert appeles == []


def test_evenement_de_scan_envoye_au_flux(dynamic_qr, monkeypatch):
    captures = []

    async def capter(event):
        captures.append(event)
        return True

    monkeypatch.setattr("apps.common.streams.push_scan", capter)
    response = get(dynamic_qr.slug)
    assert response.status_code == 302
    assert captures and captures[0]["qr_id"] == dynamic_qr.pk
    # L'IP complète ne part **pas** dans le flux : seule la troncature traverse la file.
    assert "203.0.113.7" not in str(captures[0])
    assert captures[0]["ip_trunc"] == "203.0.113.0"
    assert captures[0]["device_class"] == "mobile"


def test_un_echec_d_ingest_ne_casse_pas_la_redirection(dynamic_qr, monkeypatch, settings):
    settings.QR = {**settings.QR, "SCAN_STREAM_ENABLED": True}

    class ClientCasse:
        async def xadd(self, *a, **k):
            raise ConnectionError("redis down")

    async def indisponible(*a, **k):
        return None

    from apps.common import redis_client

    monkeypatch.setattr(redis_client, "get_redis_async", lambda: ClientCasse())
    response = get(dynamic_qr.slug)
    assert response.status_code == 302, "le scan doit passer même quand la file est morte"


def test_cache_en_panne_replie_sur_la_base(dynamic_qr, monkeypatch):

    async def remonter(exc):
        raise exc

    class CacheHS:
        async def get(self, key):
            raise ConnectionError("cache down")

    from apps.common import redis_client

    monkeypatch.setattr(redis_client, "get_redis_async", lambda: CacheHS())
    response = get(dynamic_qr.slug)
    assert response.status_code == 302
    assert response["X-Redirect-Cache"] == "miss"


def test_301_quand_l_utilisateur_le_demande(user):
    from apps.qr.models import QrCode

    qr = QrCode.objects.create(owner=user, kind="dynamic", target_url="https://kamcofarm.com/", redirect_mode="301")
    assert get(qr.slug).status_code == 301


def test_limitation_par_ip_apres_le_plafond(dynamic_qr, settings, monkeypatch):
    settings.QR = {**settings.QR, "SCAN_RATE_PER_IP": 2}

    class Compteur:
        def __init__(self):
            self.n = 0

        async def incr(self, key):
            self.n += 1
            return self.n

        async def expire(self, key, ttl):
            return None

    from apps.common import redis_client

    compteur = Compteur()
    monkeypatch.setattr(redis_client, "get_redis_async", lambda: compteur)
    from apps.redirect.views import _rate_limited

    assert asyncio.run(_rate_limited(RequestFactory().get("/r/x"), dynamic_qr.slug)) is False
    assert asyncio.run(_rate_limited(RequestFactory().get("/r/x"), dynamic_qr.slug)) is False
    assert asyncio.run(_rate_limited(RequestFactory().get("/r/x"), dynamic_qr.slug)) is True


def test_rate_limit_ne_bloque_jamais_si_redis_est_absent(dynamic_qr, monkeypatch):
    from apps.common import redis_client

    monkeypatch.setattr(redis_client, "get_redis_async", lambda: None)
    for _ in range(400):
        assert get(dynamic_qr.slug).status_code == 302
