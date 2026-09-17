"""Lectures d'agrégats — les seules que l'API de stats a le droit de faire.

Politique : on ne compte jamais à la volée dans `scan_events`. Si un trou d'agrégation est détecté
(reconstruction en arrière-plan demandée par l'admin), on renvoie ce qu'on a **et** un drapeau,
plutôt que de bloquer la requête sur un scan de table.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.db.models import Sum

from apps.analytics.models import PlatformDailyStats, QrDailyStats

COUNTRY_NAMES = {
    "FR": "France",
    "IT": "Italie",
    "BE": "Belgique",
    "CH": "Suisse",
    "ES": "Espagne",
    "DE": "Allemagne",
    "GB": "Royaume-Uni",
    "US": "États-Unis",
    "CA": "Canada",
    "MA": "Maroc",
    "TN": "Tunisie",
    "DZ": "Algérie",
    "PT": "Portugal",
    "NL": "Pays-Bas",
    "LU": "Luxembourg",
}


def _read_connection():
    """Réplique de lecture si elle est configurée, sinon la base principale (comportement de test)."""
    from django.conf import settings

    return "analytics_replica" if "analytics_replica" in settings.DATABASES else "default"


def daily_series(*, qr_id: int, since: date, until: date | None = None) -> list[dict[str, Any]]:
    until = until or date.today()
    rows = (
        QrDailyStats.objects.using(_read_connection())
        .filter(qr_id=qr_id, day__gte=since, day__lte=until)
        .values("day")
        .annotate(scans=Sum("scans"), unique=Sum("unique_visitors"))
        .order_by("day")
    )
    series = [{"day": row["day"].isoformat(), "scans": row["scans"], "unique": row["unique"]} for row in rows]
    return _fill_gaps(series, since=since, until=until)


def country_breakdown(*, qr_id: int, since: date, limit: int = 20) -> list[dict[str, Any]]:
    until = date.today()
    rows = (
        QrDailyStats.objects.using(_read_connection())
        .filter(qr_id=qr_id, day__gte=since, day__lte=until)
        .exclude(country_code="")
        .values("country_code")
        .annotate(scans=Sum("scans"))
        .order_by("-scans")[:limit]
    )
    total = sum(row["scans"] for row in rows) or 1
    return [
        {
            "code": row["country_code"],
            "name": COUNTRY_NAMES.get(row["country_code"], row["country_code"]),
            "scans": row["scans"],
            "share": round(row["scans"] / total, 4),
        }
        for row in rows
    ]


def totals_for(*, qr_id: int) -> dict[str, int]:
    row = QrDailyStats.objects.filter(qr_id=qr_id).aggregate(scans=Sum("scans"), unique=Sum("unique_visitors"))
    return {"scans": row["scans"] or 0, "unique": row["unique"] or 0}


def _fill_gaps(series: list[dict[str, Any]], *, since: date, until: date) -> list[dict[str, Any]]:
    """Un graphique sans trou : les jours à zéro sont absents de l'agrégat, on les réécrit."""
    by_day = {row["day"]: row for row in series}
    out: list[dict[str, Any]] = []
    cursor = since
    while cursor <= until:
        key = cursor.isoformat()
        out.append(by_day.get(key, {"day": key, "scans": 0, "unique": 0}))
        cursor += timedelta(days=1)
    return out


def rebuild_for_day(day: date, *, qr_ids: list[int] | None = None) -> int:
    """`INSERT ... ON CONFLICT DO UPDATE` depuis la table brute ; l'unique requis est posé en DDL.

    Sous PostgreSQL, la voie `INSERT … SELECT … ON CONFLICT` est utilisée directement : à 1 M de
    scans par jour, une boucle Python par couple (qr, pays) coûterait des dizaines de milliers d'aller-retour.
    Sous SQLite (dev et tests), on agrège en Python puis `update_or_create` — même résultat, et le
    DDL de partition n'existe pas là. Les deux chemins sont testés sur les mêmes nombres.
    """
    from django.db import connection

    ecrit = _rebuild_postgres(day, qr_ids) if connection.vendor == "postgresql" else _rebuild_python(day, qr_ids)
    # Les totaux plateforme se recalculent a partir de `QrDailyStats` (lignes deja ecrites a l'instant) :
    # une requete agregree par jour reconstruit, et le dashboard cesse de dependre du volume de lignes
    # (QR, jour, pays). Un rebuild partiel sur `qr_ids` donne neanmoins un total juste pour la journee.
    aggreger_plateforme(day)
    return ecrit


def _rebuild_python(day: date, qr_ids: list[int] | None) -> int:
    """Voie SQLite (dev, tests) : memes nombres que la voie SQL, et c'est verifie par les tests."""
    from django.db.models import Count, Q

    from apps.analytics.models import ScanEvent

    qs = ScanEvent.objects.filter(day=day)
    if qr_ids:
        qs = qs.filter(qr_id__in=qr_ids)
    # `is_bot` est retiré de *tous* les compteurs affichés et reporté dans `bot_blocked` : c'est ce
    # que fait `postgres_rebuild_sql()` (`COUNT(*) FILTER (WHERE NOT e.is_bot)`). Les deux chemins
    # — Python pour les petits volumes et le rattrapage, SQL pour la tâche de nuit — doivent produire
    # le même nombre, sinon un graphique change de valeur selon la dernière reconstruction effectuée.
    hors_bot = Q(is_bot=False)
    counts = (
        qs.values("qr_id", "country_code")
        .annotate(
            scans=Count("id", filter=hors_bot),
            uniq=Count("user_agent_hash", distinct=True, filter=hors_bot),
            mobile=Count("id", filter=hors_bot & Q(device_class="mobile")),
            desktop=Count("id", filter=hors_bot & Q(device_class="desktop")),
            tablet=Count("id", filter=hors_bot & Q(device_class="tablet")),
            bots=Count("id", filter=Q(is_bot=True)),
        )
        .order_by()
    )
    written = 0
    for row in counts:
        QrDailyStats.objects.update_or_create(
            qr_id=row["qr_id"],
            day=day,
            country_code=(row["country_code"] or ""),
            defaults={
                "scans": row["scans"],
                "unique_visitors": row["uniq"],
                "mobile": row["mobile"],
                "desktop": row["desktop"],
                "tablet": row["tablet"],
                "bot_blocked": row["bots"],
            },
        )
        written += 1
    return written


_PG_REBUILD_SQL = """
INSERT INTO analytics_qrdailystats AS s
       (qr_id, day, country_code, scans, unique_visitors, mobile, desktop, tablet, bot_blocked, updated_at)
SELECT e.qr_id,
       e.day,
       COALESCE(e.country_code, ''),
       COUNT(*) FILTER (WHERE NOT e.is_bot),
       COUNT(DISTINCT e.user_agent_hash) FILTER (WHERE NOT e.is_bot),
       COUNT(*) FILTER (WHERE e.device_class = 'mobile' AND NOT e.is_bot),
       COUNT(*) FILTER (WHERE e.device_class = 'desktop' AND NOT e.is_bot),
       COUNT(*) FILTER (WHERE e.device_class = 'tablet' AND NOT e.is_bot),
       COUNT(*) FILTER (WHERE e.is_bot),
       now()
  FROM analytics_scanevent e
 WHERE e.day = %s
 GROUP BY e.qr_id, e.day, COALESCE(e.country_code, '')
ON CONFLICT (qr_id, day, country_code) DO UPDATE
   SET scans = EXCLUDED.scans,
       unique_visitors = EXCLUDED.unique_visitors,
       mobile = EXCLUDED.mobile,
       desktop = EXCLUDED.desktop,
       tablet = EXCLUDED.tablet,
       bot_blocked = EXCLUDED.bot_blocked,
       updated_at = now()
"""

# Deux variantes plutot qu'une concatenation conditionnelle : la requete reste une constante, bandit
# n'a plus de motif de signaler une construction par chaine, et on ne peut pas oublier un `AND`.
_PG_REBUILD_SQL_QRS = _PG_REBUILD_SQL.replace(
    " WHERE e.day = %s\n",
    " WHERE e.day = %s\n   AND e.qr_id = ANY(%s)\n",
)


def postgres_rebuild_query(day: date, qr_ids: list[int] | None = None) -> tuple[str, list]:
    """La requête et ses paramètres — le jour passe **paramétré**, jamais formaté dans la chaîne.

    C'est ce qui rend l'assertion de `tests/test_analytics.py` utile : on vérifie qu'aucune valeur
    n'est interpolée (`B608` de bandit était déclenché par la version en f-string, et l'avoir fait
    taire par un `nosec` aurait caché le seul endroit du projet où du SQL est écrit à la main).
    """
    if qr_ids:
        return _PG_REBUILD_SQL_QRS, [day, list(qr_ids)]
    return _PG_REBUILD_SQL, [day]


def _rebuild_postgres(day: date, qr_ids: list[int] | None) -> int:
    from django.db import connection

    requete, params = postgres_rebuild_query(day, qr_ids)
    with connection.cursor() as curseur:
        curseur.execute(requete, params)
        # `rowcount` couvre `DO UPDATE` : Postgres compte les lignes renvoyées par la commande, donc
        # insertions et fondus. C'est le nombre que la tâche de nuit affiche.
        return max(0, curseur.rowcount)


def rebuild_for_day_postgres(day: date, *, qr_ids: list[int] | None = None) -> int:
    """Point d'entrée explicite de la voie SQL (utilisé par `rebuild_for_day` sous PostgreSQL)."""
    return _rebuild_postgres(day, qr_ids)


def aggreger_plateforme(day: date) -> dict[str, int]:
    """Totaux de la journee dans `PlatformDailyStats` — la table que lit le back-office.

    Le melange statique/dynamique et la provenance (admin ou client) ne sont pas dans `QrDailyStats` :
    on les recupere par une deuxieme requete sur les seuls identifiants vus ce jour-la, ce qui reste
    borne par le nombre de QR scannes et non par le nombre de lignes de scan.
    """
    par_qr = list(
        QrDailyStats.objects.filter(day=day)
        .values("qr_id")
        .annotate(
            scans=Sum("scans"),
            uniques=Sum("unique_visitors"),
            mobile=Sum("mobile"),
            desktop=Sum("desktop"),
            tablette=Sum("tablet"),
            bots=Sum("bot_blocked"),
        )
        .order_by()
    )
    from apps.qr.models import QrCode

    meta = {
        ligne["pk"]: (ligne["kind"], ligne["origine"])
        for ligne in QrCode.objects.filter(pk__in=[row["qr_id"] for row in par_qr]).values("pk", "kind", "origine")
    }
    totaux = {
        "scans": 0,
        "visiteurs_uniques": 0,
        "mobile": 0,
        "desktop": 0,
        "tablette": 0,
        "bots_bloques": 0,
        "qr_uses_statiques": 0,
        "qr_uses_dynamiques": 0,
        "qr_uses_admin": 0,
        "qr_actifs": len(par_qr),
    }
    # (cle de l'annotation, champ du modele). Attention au sens de la fleche : l'inversion a deja ete
    # faite ici, et elle se traduisait par un `KeyError` sur chaque reconstruction de nuit.
    # Un seul passage sur les lignes, et les cles en litteraux : la table de correspondance en tuples
    # rendait l'indexation impossible a verifier pour mypy (le TypedDict d'une annotation DRF/SQL exige des
    # cles litterales), et six lignes ecrites a la main se relisent mieux qu'une indirection de plus.
    for row in par_qr:
        totaux["scans"] += row["scans"] or 0
        # `visiteurs_uniques` est une SOMME de visiteurs uniques par (QR, pays) : un meme visiteur compte
        # une fois par QR. Borne haute assumee — le deduplicata plateforme exigerait un HLL global, et la
        # valeur `unique` d'un QR reste exacte la ou le client la regarde.
        totaux["visiteurs_uniques"] += row["uniques"] or 0
        totaux["mobile"] += row["mobile"] or 0
        totaux["desktop"] += row["desktop"] or 0
        totaux["tablette"] += row["tablette"] or 0
        totaux["bots_bloques"] += row["bots"] or 0
        kind, origine = meta.get(row["qr_id"], ("static", "client"))
        if kind == "dynamic":
            totaux["qr_uses_dynamiques"] += row["scans"] or 0
        else:
            totaux["qr_uses_statiques"] += row["scans"] or 0
        if origine == "admin":
            totaux["qr_uses_admin"] += row["scans"] or 0
    PlatformDailyStats.objects.update_or_create(day=day, defaults=totaux)
    return totaux


def serie_plateforme(*, since: date, until: date | None = None) -> list[dict[str, Any]]:
    """La serie journaliere des totaux, trous combles a zero : un graphique sans trou n'est pas une vue."""
    until = until or date.today()
    lignes = PlatformDailyStats.objects.using(_read_connection()).filter(day__gte=since, day__lte=until).order_by("day")
    par_jour = {ligne.day: ligne for ligne in lignes}
    serie = []
    jour = since
    while jour <= until:
        ligne = par_jour.get(jour)
        serie.append(
            {
                "day": jour.isoformat(),
                "scans": ligne.scans if ligne else 0,
                "unique": ligne.visiteurs_uniques if ligne else 0,
                "mobile": ligne.mobile if ligne else 0,
                "desktop": ligne.desktop if ligne else 0,
                "dynamique": ligne.qr_uses_dynamiques if ligne else 0,
                "statique": ligne.qr_uses_statiques if ligne else 0,
                "admin": ligne.qr_uses_admin if ligne else 0,
                "bots": ligne.bots_bloques if ligne else 0,
                "qr_actifs": ligne.qr_actifs if ligne else 0,
            }
        )
        jour += timedelta(days=1)
    return serie


def totaux_plateforme(*, since: date, until: date | None = None) -> dict[str, int]:
    until = until or date.today()
    agregats = (
        PlatformDailyStats.objects.using(_read_connection())
        .filter(day__gte=since, day__lte=until)
        .aggregate(
            scans=Sum("scans"),
            visiteurs_uniques=Sum("visiteurs_uniques"),
            mobile=Sum("mobile"),
            desktop=Sum("desktop"),
            tablette=Sum("tablette"),
            bots=Sum("bots_bloques"),
            statiques=Sum("qr_uses_statiques"),
            dynamiques=Sum("qr_uses_dynamiques"),
            admin=Sum("qr_uses_admin"),
        )
    )
    return {cle: valeur or 0 for cle, valeur in agregats.items()}


def repartition_pays(*, since: date, until: date | None = None, limit: int = 12) -> list[dict[str, Any]]:
    """Repartition par pays, lue dans `QrDailyStats` avec un `LIMIT` : le dashboard n'a rien a gagner
    a trainer les 250 pays, et un tri suivi d'un `[:limit]` en Python serait un scan complet."""
    until = until or date.today()
    lignes = (
        QrDailyStats.objects.using(_read_connection())
        .filter(day__gte=since, day__lte=until)
        .exclude(country_code="")
        .values("country_code")
        .annotate(scans=Sum("scans"), visiteurs=Sum("unique_visitors"))
        .order_by("-scans")[:limit]
    )
    return [
        {
            "code": ligne["country_code"],
            "nom": COUNTRY_NAMES.get(ligne["country_code"], ligne["country_code"]),
            "scans": ligne["scans"],
            "visiteurs": ligne["visiteurs"],
        }
        for ligne in lignes
    ]
