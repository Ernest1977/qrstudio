from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from apps.analytics.aggregates import rebuild_for_day


class Command(BaseCommand):
    help = "Recalcule analytics_qrdailystats à partir des lignes de scan brutes."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="since", help="date de début (AAAA-MM-JJ), défaut: -30 j")
        parser.add_argument("--to", dest="until", help="date de fin, défaut: aujourd'hui")
        parser.add_argument("--qr", type=int, help="restreindre à un QR")

    def handle(self, *args, **options):
        today = date.today()
        since = date.fromisoformat(options["since"]) if options["since"] else today - timedelta(days=30)
        until = date.fromisoformat(options["until"]) if options["until"] else today
        if since > until:
            self.stderr.write(self.style.ERROR("--from postérieur à --to."))
            return 1
        qr_ids = [options["qr"]] if options["qr"] else None
        written = 0
        cursor = since
        while cursor <= until:
            written += rebuild_for_day(cursor, qr_ids=qr_ids)
            cursor += timedelta(days=1)
        self.stdout.write(
            self.style.SUCCESS(f"{written} ligne(s) d'agrégat(s) recalculée(s) sur {until - since} jours.")
        )
        return 0
