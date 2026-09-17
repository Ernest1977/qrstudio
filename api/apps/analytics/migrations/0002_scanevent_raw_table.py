"""Crée `analytics_scanevent` en SQL natif : partitionnée par mois sur PostgreSQL, plate ailleurs.

Le modèle est `managed = False` volontairement — un `CREATE TABLE` généré par Django ne sait pas
déclarer `PARTITION BY RANGE`, et c'est justement la propriété qui rend la purge RGPD instantanée
(`DROP PARTITION`) et l'index de lecture utile. La migration est **idempotente** et réversible :
revenir dessus ne supprime pas les données (une table d'audit ne se détruit pas en rollback).
"""

from __future__ import annotations

from django.db import migrations

from apps.analytics import sql


def create_schema(apps, schema_editor):
    connection = schema_editor.connection
    report = sql.ensure_schema(connection)
    if connection.vendor == "postgresql":
        # La partition du mois courant + celle du mois suivant, pour qu'un redéploiement un jour de
        # fin de mois ne se retrouve pas avec une écriture rejetée (`no partition of relation found`).
        with connection.cursor() as cursor:
            cursor.execute(sql.attach_next_month_sql())


def noop_reverse(apps, schema_editor):  # pragma: no cover - volontairement vide
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0001_initial"),
        ("qr", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(create_schema, noop_reverse),
    ]
