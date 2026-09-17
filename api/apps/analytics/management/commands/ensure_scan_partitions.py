"""Crée la partition du mois courant et du mois suivant (PostgreSQL uniquement).

Échec non critique : la partition `DEFAULT` absorbe les lignes, donc un `beat` en panne ne perd **aucun**
scan — il rend juste le `DROP PARTITION` de purge moins tranchant. C'est le bon compromis pour un job
qui tourne sur un cron de VPS.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import connection

from apps.analytics import sql


class Command(BaseCommand):
    help = "Garantit l'existence des partitions mensuelles de analytics_scanevent."

    def add_arguments(self, parser):
        parser.add_argument("--months-ahead", type=int, default=1, help="Au-delà du mois courant.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING(f"vendor={connection.vendor} : rien à faire (pas de partitionnement).")
            )
            return 0
        statements = [sql.default_partition_sql(), sql.create_partition_sql(first_day=date.today())]
        horizon = date.today().replace(day=1)
        for _ in range(max(0, options["months_ahead"])):
            horizon = (horizon + timedelta(days=32)).replace(day=1)
            statements.append(
                f"CREATE TABLE IF NOT EXISTS {sql.partition_name(horizon)} "
                f"PARTITION OF analytics_scanevent FOR VALUES FROM ('{horizon.isoformat()}') "
                f"TO ('{(horizon + timedelta(days=32)).replace(day=1).isoformat()}')"
            )
        if options["dry_run"]:
            for statement in statements:
                self.stdout.write(statement)
            return 0
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        self.stdout.write(self.style.SUCCESS(f"{len(statements)} instruction(s) de partition appliquée(s)."))
        return 0

    # `--dry-run` sert à relire le SQL en revue de déploiement sans ouvrir de transaction.
