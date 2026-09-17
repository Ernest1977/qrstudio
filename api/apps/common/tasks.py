"""Tâches Celery du projet (hors analytics, qui a les siennes)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.common.run_csv_export", bind=True, max_retries=2, default_retry_delay=30)
def run_csv_export(self, token: str) -> dict:
    from apps.common import export

    payload = export.consume_token(token)
    if payload is None:
        logger.warning("export sans jeton valide (expire ?)")
        return {"status": "expired"}
    content = export.build_csv(payload["model"], payload["pks"])
    path = export.write_export_file(token, content)
    return {"status": "ok", "rows": content.count("\n") - 1, "bytes": path.stat().st_size, "path": str(path)}
