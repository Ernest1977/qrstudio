"""Purge RGPD des adresses tronquées (et ville/région) au-delà de la rétention."""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analytics.models import ScanEvent


class Command(BaseCommand):
    help = "Efface ip_trunc/region/city des scans plus vieux que QR['IP_RETENTION_HOURS']."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, help="force la rétention (sinon réglage QR.IP_RETENTION_HOURS)")
        parser.add_argument("--batch", type=int, default=20_000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from django.conf import settings

        hours = options["hours"] or int(str((settings.QR or {}).get("IP_RETENTION_HOURS", 24)))
        cutoff = timezone.now() - timedelta(hours=hours)
        qs = ScanEvent.objects.filter(ts__lt=cutoff).exclude(ip_trunc__isnull=True)
        count = qs.count()
        if options["dry_run"]:
            self.stdout.write(
                f"{count} ligne(s) portent encore une adresse tronquée, avant {cutoff:%Y-%m-%d %H:%M} UTC."
            )
            return 0
        updated = 0
        while True:
            pks = list(qs.values_list("pk", "ts")[: options["batch"]])
            if not pks:
                break
            ids = [row[0] for row in pks]
            updated += ScanEvent.objects.filter(pk__in=ids).update(
                ip_trunc=None, ip_prefix_len=None, region=None, city=None
            )
        self.stdout.write(
            self.style.SUCCESS(f"{updated} adresse(s) tronquée(s) effacée(s) (avant {cutoff:%Y-%m-%d %H:%M} UTC).")
        )
        return 0
