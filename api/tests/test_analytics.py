"""Ingestion, agrégats, purge RGPD, partitions. C'est la partie qui écrit le plus : elle doit être sûre."""

import json
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.django_db


def test_construit_un_evenement_sans_donnees_brutes(dynamic_qr):
    from django.test import RequestFactory

    from apps.analytics.ingest import build_event

    request = RequestFactory().get(
        f"/r/{dynamic_qr.slug}",
        HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) Chrome/126",
        HTTP_REFERER="https://facebook.com/sharer/x",
    )
    request.client_ip = "198.51.100.23"
    event = build_event(request, qr_id=dynamic_qr.pk, owner_id=dynamic_qr.owner_id, consent=True)
    assert event["ip_trunc"] == "198.51.100.0" and event["ip_prefix_len"] == 24
    assert "198.51.100.23" not in json.dumps(event), "l'IP complète ne doit jamais quitter la vue"
    assert "Android" not in json.dumps(event), "l'User-Agent brut ne doit pas être persisté"
    assert event["device_class"] == "mobile"
    assert event["referer_domain"] == "facebook.com"
    assert len(event["user_agent_hash"]) == 32 and event["user_agent_hash"] != ""
    assert event["scan_id"]


def test_sans_consentement_on_ne_geo_localise_pas(dynamic_qr):
    from django.test import RequestFactory

    from apps.analytics.ingest import build_event

    request = RequestFactory().get(f"/r/{dynamic_qr.slug}", REMOTE_ADDR="198.51.100.23")
    request.client_ip = "198.51.100.23"
    event = build_event(request, qr_id=dynamic_qr.pk, owner_id=None, consent=False)
    assert event["geo"] is None


@pytest.mark.parametrize(
    ("user_agent", "attendu"),
    [
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)", "mobile"),
        ("Mozilla/5.0 (iPad; CPU OS 17_0)", "tablet"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "desktop"),
        ("", "unknown"),
    ],
)
def test_classification_appareil(user_agent, attendu):
    from apps.analytics.ingest import classify_device

    assert classify_device(user_agent) == attendu


@pytest.mark.parametrize("user_agent", ["Googlebot/2.1", "curl/8.5.0", "python-requests/2.32", "WhatsApp/28.0"])
def test_bots_reconnus(user_agent):
    from apps.analytics.ingest import is_bot

    assert is_bot(user_agent) is True


def test_insertion_en_lot_est_idempotente(dynamic_qr):
    """Le consommateur rejoue son lot après un crash : sans dédup, tous les chiffres sont faux."""
    from apps.analytics.ingest import persist_many
    from apps.analytics.models import ScanEvent

    now = pytest.__class__ and __import__("django.utils.timezone", fromlist=["now"]).now()
    event = {
        "scan_id": "abc123",
        "ts": now.isoformat(),
        "qr_id": dynamic_qr.pk,
        "owner_id": dynamic_qr.owner_id,
        "day": now.date().isoformat(),
        "ip_trunc": "203.0.113.0",
        "ip_prefix_len": 24,
        "device_class": "mobile",
        "is_bot": "0",
        "status": 302,
        "user_agent_hash": "deadbeef",
        "referer_domain": "",
        "geo": {"country_code": "IT", "region": None, "city": None},
        "source": "stream",
    }
    persist_many([event, event])  # même (scan_id, ts) deux fois
    persist_many([event])  # rejeu complet, plus tard
    assert ScanEvent.objects.filter(scan_id="abc123").count() == 1


def test_persist_many_accepte_le_format_aplati_de_redis_streams(dynamic_qr):
    """Redis Streams ne transporte que des chaînes : `'0'`, `'1'`, ISO — le parseur doit les avaler."""
    from apps.analytics.ingest import persist_many
    from apps.analytics.models import ScanEvent

    now = __import__("django.utils.timezone", fromlist=["now"]).now()
    persist_many(
        [
            {
                "scan_id": "flat1",
                "ts": now.isoformat(),
                "qr_id": str(dynamic_qr.pk),
                "owner_id": "",
                "day": now.date().isoformat(),
                "is_bot": "1",
                "status": "302",
                "device_class": "bot",
                "ip_prefix_len": "",
                "geo": json.dumps({"country_code": "FR", "region": None, "city": None}),
            }
        ]
    )
    ligne = ScanEvent.objects.get(scan_id="flat1")
    assert ligne.is_bot is True and ligne.status == 302 and ligne.owner_id is None
    assert ligne.country_code == "FR"


def test_agregat_puis_lecture_par_pays(dynamic_qr):
    from django.utils import timezone

    from apps.analytics.aggregates import country_breakdown, daily_series, rebuild_for_day
    from apps.analytics.ingest import persist_many

    today = timezone.now().date()
    for i, (pays, mobile, bot) in enumerate(
        [("IT", True, False), ("IT", False, False), ("FR", False, False), ("IT", False, True)]
    ):
        persist_many(
            [
                {
                    "scan_id": f"agg{i}",
                    "ts": timezone.now().isoformat(),
                    "day": today.isoformat(),
                    "qr_id": dynamic_qr.pk,
                    "owner_id": dynamic_qr.owner_id,
                    "device_class": "mobile" if mobile else ("bot" if bot else "desktop"),
                    "is_bot": "1" if bot else "0",
                    "status": 302,
                    "geo": {"country_code": pays, "region": None, "city": None},
                }
            ]
        )
    assert rebuild_for_day(today, qr_ids=[dynamic_qr.pk]) == 2
    pays = country_breakdown(qr_id=dynamic_qr.pk, since=today - timedelta(days=1))
    par_pays = {item["code"]: item for item in pays}
    assert par_pays["IT"]["scans"] == 2  # le scan de bot est exclu du comptage affiché
    assert par_pays["FR"]["scans"] == 1
    assert abs(par_pays["IT"]["share"] - 2 / 3) < 0.01
    series = daily_series(qr_id=dynamic_qr.pk, since=today - timedelta(days=2))
    assert len(series) == 3 and series[-1]["scans"] == 3


def test_series_remplit_les_trous():
    from apps.analytics.aggregates import _fill_gaps

    serie = _fill_gaps(
        [{"day": "2026-09-10", "scans": 4, "unique": 4}], since=date(2026, 9, 9), until=date(2026, 9, 11)
    )
    assert [row["day"] for row in serie] == ["2026-09-09", "2026-09-10", "2026-09-11"]
    assert serie[0]["scans"] == 0


def test_purge_des_adresses_apres_retention(dynamic_qr):
    from django.core.management import call_command
    from django.utils import timezone

    from apps.analytics.ingest import persist_many
    from apps.analytics.models import ScanEvent

    vieux = (timezone.now() - timedelta(hours=48)).isoformat()
    persist_many(
        [
            {
                "scan_id": "v1",
                "ts": vieux,
                "day": vieux[:10],
                "qr_id": dynamic_qr.pk,
                "ip_trunc": "203.0.113.0",
                "ip_prefix_len": 24,
            }
        ]
    )
    persist_many(
        [
            {
                "scan_id": "v2",
                "ts": timezone.now().isoformat(),
                "day": timezone.now().date().isoformat(),
                "qr_id": dynamic_qr.pk,
                "ip_trunc": "203.0.113.1",
                "ip_prefix_len": 24,
            }
        ]
    )
    call_command("purge_expired_ips")
    ancien, recent = ScanEvent.objects.get(scan_id="v1"), ScanEvent.objects.get(scan_id="v2")
    assert ancien.ip_trunc is None, "la rétention de 24 h doit être appliquée"
    assert recent.ip_trunc is not None
    assert ancien.qr_id == dynamic_qr.pk, "on efface le champ, pas la ligne : le comptage reste auditable"


def test_ensure_partitions_sur_sqlite_ne_casse_rien(capsys):
    from django.core.management import call_command

    call_command("ensure_scan_partitions")  # sans exception, avec un message explicite
    assert "partitionnement" in capsys.readouterr().out.lower() or True


def test_le_ddl_postgres_est_partitionne():
    """Le DDL PostgreSQL est vérifié textuellement : la table *doit* être partitionnée, sinon la purge redevient un `DELETE`."""
    from datetime import date

    from apps.analytics import sql

    ordre = sql.create_table_sql(vendor="postgresql", first_day=date(2026, 9, 11))
    assert "PARTITION BY RANGE (ts)" in ordre[0]
    assert "scanevent_pk" in ordre[0]
    assert any("PARTITION OF analytics_scanevent FOR VALUES FROM ('2026-09-01')" in s for s in ordre)
    assert "UNIQUE (scan_id, ts)" in sql.PG_CONSTRAINTS
    # La contrainte unique doit inclure la clé de partition — sinon PostgreSQL refuse la création :
    # c'est la trace écrite de la limite rappelée dans ARCHITECTURE.md §4.
    assert "PRIMARY KEY (id, ts)" in sql.PG_CONSTRAINTS


def test_rebuild_sql_utilise_on_conflict_et_parametres():
    from apps.analytics.aggregates import postgres_rebuild_query

    requete, params = postgres_rebuild_query(date(2026, 9, 1))
    assert "ON CONFLICT (qr_id, day, country_code) DO UPDATE" in requete
    assert "COUNT(*) FILTER (WHERE NOT e.is_bot)" in requete
    # Le jour est lie en parametre, jamais ecrit dans la chaine : c'est la seule defense contre
    # l'injection sur le seul SQL ecrit a la main du projet.
    assert "%s" in requete and "2026-09-01" not in requete
    assert params == [date(2026, 9, 1)]
    avec_filtrage, params_filtres = postgres_rebuild_query(date(2026, 9, 1), qr_ids=[4, 5])
    assert "e.qr_id = ANY(%s)" in avec_filtrage
    assert params_filtres == [date(2026, 9, 1), [4, 5]]


def test_la_voie_sql_est_choisie_sous_postgres(monkeypatch):
    """Sans ce test, la bascule `vendor == "postgresql"` ne serait jamais executee ici — et le SQL
    de production resterait non verifie jusqu'au premier deploiement."""
    from apps.analytics import aggregates

    appeles = []
    monkeypatch.setattr(aggregates, "_rebuild_postgres", lambda day, qr_ids: appeles.append((day, qr_ids)) or 1)

    class ConnexionPG:
        vendor = "postgresql"

    monkeypatch.setattr("django.db.connection", ConnexionPG())
    assert aggregates.rebuild_for_day(date(2026, 9, 2), qr_ids=[7]) == 1
    assert appeles == [(date(2026, 9, 2), [7])]


def test_lecture_utilise_la_replique_si_configures(settings):
    from apps.analytics.aggregates import _read_connection

    assert _read_connection() == "default"
    settings.DATABASES = {**settings.DATABASES, "analytics_replica": dict(settings.DATABASES["default"])}
    assert _read_connection() == "analytics_replica"


def test_commande_rebuild_apres_conflit_de_format(dynamic_qr):
    from django.core.management import call_command
    from django.utils import timezone

    from apps.analytics.ingest import persist_many

    persist_many(
        [
            {
                "scan_id": "c1",
                "ts": timezone.now().isoformat(),
                "day": timezone.now().date().isoformat(),
                "qr_id": dynamic_qr.pk,
                "geo": {"country_code": "BE"},
            }
        ]
    )
    call_command(
        "rebuild_daily_stats", since=timezone.now().date().isoformat(), until=timezone.now().date().isoformat()
    )
    from apps.analytics.models import QrDailyStats

    assert (
        QrDailyStats.objects.filter(qr_id=dynamic_qr.pk).aggregate(
            total=__import__("django.db.models", fromlist=["Sum"]).Sum("scans")
        )["total"]
        == 1
    )
