"""Reporte les compteurs Redis (`qrs:scans:<slug>`) vers `QrCode.scan_count_total`.

`scan_count_total` est une **vue d'ensemble**, pas une source de vérité : les totaux exacts se
reconstruisent depuis les lignes brutes (`rebuild_daily_stats`). On accepte donc une perte de
compteur si Redis est vidé — au prix d'un rattrapage explicite, et non d'une écriture SQL par scan.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.common import redis_client
from apps.qr.models import QrCode


class Command(BaseCommand):
    help = "Écrase les compteurs de scan en base depuis Redis."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        client = redis_client.get_redis()
        if client is None:
            self.stdout.write(
                self.style.WARNING("Redis absent : compteurs non rafraîchis (la base reste la vérité de rattrapage).")
            )
            return 0
        updated = 0
        for key in client.scan_iter(match="qrs:scans:*", count=1000):
            slug = key.decode().split(":")[-1] if isinstance(key, bytes) else key.split(":")[-1]
            raw = client.get(key)
            if raw is None:
                continue
            value = int(raw)
            if options["dry_run"]:
                self.stdout.write(f"{slug}: {value}")
                continue
            updated += QrCode.objects.filter(slug=slug).update(scan_count_total=value)
        self.stdout.write(self.style.SUCCESS(f"{updated} QR(s) mis à jour depuis les compteurs Redis."))
        return 0
