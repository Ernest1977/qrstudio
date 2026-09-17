"""DDL des événements de scan.

`scan_events` est une table **native PostgreSQL partitionnée par mois**, créée en SQL brut et non
par les migrations Django (`managed = False`). Raisons, toutes mesurables :

* un index `UNIQUE` sur une table partitionnée doit inclure la clé de partition : `scan_id` seul ne
  peut donc pas être unique — la déduplication se fait par `(scan_id, ts)` + une requête de contrôle,
  et le consommateur est idempotent (testé) ;
* `DROP PARTITION` pour la purge RGPD est instantané, contrairement à un `DELETE FROM ... WHERE ts < ?`
  sur des dizaines de millions de lignes ;
* sur SQLite (tests, dev) il n'y a pas de partitionnement : on crée une table **similaire**, ce qui
  permet de tester toute la logique d'agrégation sans Postgres. C'est volontaire : la différence est
  limitée au DDL, et le test `test_partition_ddl` vérifie le SQL PostgreSQL textuellement.
"""

from __future__ import annotations

from datetime import date
from typing import Any

COLUMNS = """
    id              bigserial,
    ts              timestamptz  NOT NULL,
    qr_id           bigint       NOT NULL,
    owner_id        bigint,
    day             date         NOT NULL,
    country_code    varchar(2),
    region          varchar(3),
    city            varchar(96),
    ip_trunc        inet,
    ip_prefix_len   smallint,
    user_agent_hash bit(128),
    referer_domain  varchar(190),
    device_class    varchar(12)  NOT NULL DEFAULT 'unknown',
    is_bot          boolean      NOT NULL DEFAULT false,
    status          smallint     NOT NULL DEFAULT 302,
    scan_id         varchar(32)  NOT NULL,
    source          varchar(16)  NOT NULL DEFAULT 'stream'
"""

PG_CONSTRAINTS = """
    ,CONSTRAINT scanevent_pk PRIMARY KEY (id, ts),
    CONSTRAINT scanevent_scan_unique UNIQUE (scan_id, ts)
"""

SQLITE_CONSTRAINTS = """
    ,PRIMARY KEY (id),
    UNIQUE (scan_id, ts)
"""

PG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS scanevent_qr_ts_idx ON analytics_scanevent (qr_id, ts DESC)",
    "CREATE INDEX IF NOT EXISTS scanevent_day_country_idx ON analytics_scanevent (day, country_code, qr_id)",
    "CREATE INDEX IF NOT EXISTS scanevent_ip_expiry_idx ON analytics_scanevent (ts) WHERE ip_trunc IS NOT NULL",
]


def partition_name(day: date) -> str:
    return f"analytics_scanevent_{day:%Y%m}"


def create_table_sql(*, vendor: str, first_day: date, months_ahead: int = 1) -> list[str]:
    """Instructions de création, dans l'ordre. Sur SQLite : une table plate, pas de partition."""
    if vendor == "postgresql":
        return [
            f"CREATE TABLE IF NOT EXISTS analytics_scanevent ({COLUMNS}{PG_CONSTRAINTS}) PARTITION BY RANGE (ts)",
            *[
                f"CREATE INDEX IF NOT EXISTS {name} ON analytics_scanevent ({cols})"
                for name, cols in PG_INDEXES_named()
            ],
            *[create_partition_sql(months_ahead, first_day=first_day)],
        ]
    return [
        "CREATE TABLE IF NOT EXISTS analytics_scanevent ("
        " id integer PRIMARY KEY AUTOINCREMENT,"
        " ts datetime NOT NULL, qr_id bigint NOT NULL, owner_id bigint NULL,"
        " day date NOT NULL, country_code varchar(2) NULL, region varchar(3) NULL,"
        " city varchar(96) NULL, ip_trunc varchar(45) NULL, ip_prefix_len smallint NULL,"
        " user_agent_hash varchar(32) NULL, referer_domain varchar(190) NULL,"
        " device_class varchar(12) NOT NULL DEFAULT 'unknown', is_bot bool NOT NULL DEFAULT 0,"
        " status smallint NOT NULL DEFAULT 302, scan_id varchar(32) NOT NULL,"
        " source varchar(16) NOT NULL DEFAULT 'stream',"
        " UNIQUE (scan_id, ts))",
        "CREATE INDEX IF NOT EXISTS scanevent_qr_ts_idx ON analytics_scanevent (qr_id, ts DESC)",
        "CREATE INDEX IF NOT EXISTS scanevent_day_country_idx ON analytics_scanevent (day, country_code, qr_id)",
    ]


def PG_INDEXES_named():
    """Noms + colonnes, pour générer les `CREATE INDEX` sur la table mère (ils se propagent)."""
    return [
        ("scanevent_qr_ts_idx", "qr_id, ts DESC"),
        ("scanevent_day_country_idx", "day, country_code, qr_id"),
        ("scanevent_ip_expiry_idx", "ts"),
    ]


def create_partition_sql(months_ahead: int = 1, *, first_day: date | None = None) -> str:
    """Un seul `FOR VALUES` multi-partitions n'existe pas : on renvoie l'instruction du mois courant.

    Les mois suivants sont créés par la tâche `ensure_scan_partitions` (6 h) — et la *partition par
    défaut* absorbe tout ce qui arrive trop tôt, ce qui rend l'automate non critique.
    """
    today = first_day or date.today()
    from datetime import timedelta

    start = today.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return (
        f"CREATE TABLE IF NOT EXISTS {partition_name(start)} "
        f"PARTITION OF analytics_scanevent FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
    )


def default_partition_sql() -> str:
    return "CREATE TABLE IF NOT EXISTS analytics_scanevent_default PARTITION OF analytics_scanevent DEFAULT"


def attach_next_month_sql() -> str:
    from datetime import timedelta

    start = (date.today().replace(day=1) + timedelta(days=32)).replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return (
        f"CREATE TABLE IF NOT EXISTS {partition_name(start)} "
        f"PARTITION OF analytics_scanevent FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
    )


def run_sql(connection: Any, statements: list[str]) -> int:
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return len(statements)


def ensure_schema(connection: Any) -> dict[str, Any]:
    """Idempotent : appelé par la migration et par `ensure_scan_partitions`."""
    vendor = connection.vendor
    statements = create_table_sql(vendor=vendor, first_day=date.today())
    if vendor == "postgresql":
        statements = [statements[0], default_partition_sql(), *statements[1:]]
    executed = run_sql(connection, statements)
    return {"vendor": vendor, "statements": executed, "partitioned": vendor == "postgresql"}
