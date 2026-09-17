"""Tâches planifiées. Toutes idempotentes : un doublon d'exécution ne doit rien casser.

Le `beat` tourne dans son propre conteneur : si un worker meurt en pleine tâche, la suivante repart
de zéro sans dette (les agrégats se reconstruisent à partir de la table brute, pas d'un curseur).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.analytics.ingest_scans", bind=True, max_retries=3, default_retry_delay=5)
def ingest_scans(self, *, batch: int = 1000) -> dict:
    """Consomme la file de scan. Cadence : appelée par un worker dédié en boucle (pas par `beat`)."""
    from apps.analytics.ingest import consume_stream

    inserted, skipped = consume_stream(count=batch)
    if inserted:
        logger.info("ingest insere=%s ignores=%s", inserted, skipped)
    return {"inserted": inserted, "skipped": skipped}


@shared_task(name="apps.analytics.rebuild_daily_stats")
def rebuild_daily_stats(*, qr_id: int | None = None, days: int = 3) -> dict:
    """Recalcule les N derniers jours — la réponse au « mes chiffres sont faux » et au trou d'ingest."""
    from datetime import date

    from apps.analytics.aggregates import rebuild_for_day

    today = date.today()
    written = 0
    for offset in range(days + 1):
        written += rebuild_for_day(today - timedelta(days=offset), qr_ids=[qr_id] if qr_id else None)
    return {"rows": written, "days": days + 1, "qr_id": qr_id}


@shared_task(name="apps.analytics.ensure_scan_partitions")
def ensure_scan_partitions() -> dict:
    from io import StringIO

    from django.core.management import call_command

    call_command("ensure_scan_partitions", stdout=StringIO())
    return {"status": "ok"}


@shared_task(name="apps.analytics.purge_expired_ips")
def purge_expired_ips() -> dict:
    """Efface `ip_trunc`/`city`/`region` au-delà de la rétention : l'empreinte suffit à la dédup.

    On garde la ligne (le comptage doit rester auditable) et on détruit le champ personnel : c'est
    la minimisation *dans le temps*, pas seulement à l'écriture.
    """
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("purge_expired_ips", stdout=out)
    return {"report": out.getvalue().strip()}


@shared_task(name="apps.analytics.flush_scan_counters")
def flush_scan_counters() -> dict:
    """Reporte les compteurs Redis de scan vers `QrCode.scan_count_total` (vue d'ensemble du front)."""
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("flush_scan_counters", stdout=out)
    return {"report": out.getvalue().strip()}
