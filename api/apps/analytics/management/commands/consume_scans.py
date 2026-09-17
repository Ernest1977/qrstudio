"""Boucle d'ingestion manuelle : `manage.py consume_scans --follow`.

En production c'est un worker Celery qui l'appelle ; la commande sert au diagnostic (et au sprint 3,
où l'on mesurera le débit réel avant de la passer en `XREADGROUP` multi-consommateurs).
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.analytics.ingest import consume_stream


class Command(BaseCommand):
    help = "Vide la file de scan vers la base."

    def add_arguments(self, parser):
        parser.add_argument("--follow", action="store_true", help="boucle infinie")
        parser.add_argument("--batch", type=int, default=1000)
        parser.add_argument("--sleep", type=float, default=1.0)

    def handle(self, *args, **options):
        while True:
            inserted, skipped = consume_stream(count=options["batch"])
            self.stdout.write(f"insérés={inserted} ignorés={skipped}")
            if not options["follow"]:
                return 0
            time.sleep(options["sleep"])
