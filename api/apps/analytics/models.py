"""Lignes de scan brutes + agrégat quotidien.

La table brute n'est **jamais** lue par l'API des statistiques : un graphique sur 12 mois contre
`scan_events` coûterait un scan parallèle de dizaines de Go. Tout passe par `QrDailyStats`,
reconstruit incrémentalement (et entièrement à la demande depuis l'admin).
"""

from __future__ import annotations

from django.db import models


class DeviceClass(models.TextChoices):
    MOBILE = "mobile", "Mobile"
    DESKTOP = "desktop", "Ordinateur"
    TABLET = "tablet", "Tablette"
    BOT = "bot", "Robot"
    UNKNOWN = "unknown", "Inconnu"


class ScanSource(models.TextChoices):
    STREAM = "stream", "File de scan"
    ACCESS_LOG = "access_log", "Journal Caddy"
    MANUAL = "manual", "Correction manuelle"


class ScanEvent(models.Model):
    """Un scan = une ligne. `managed=False` : la table est partitionnée, voir apps/analytics/sql.py."""

    ts = models.DateTimeField("horodatage", db_index=True)
    qr_id = models.BigIntegerField("QR")
    owner_id = models.BigIntegerField(null=True, blank=True)
    day = models.DateField("jour")
    # `NULL` ≠ `''` : NULL veut dire « pas de consentement donc pas de géo », '' veut dire
    # « géo demandée, pays non résolu ». C'est ce qui permet d'écrire en SQL `WHERE country_code IS
    # NULL` pour prouver qu'aucune donnée a été prise sans base légale — d'où la dérogation DJ001.
    country_code = models.CharField(max_length=2, null=True, blank=True)
    region = models.CharField(max_length=3, null=True, blank=True)
    city = models.CharField(max_length=96, null=True, blank=True)
    ip_trunc = models.GenericIPAddressField(null=True, blank=True)
    ip_prefix_len = models.PositiveSmallIntegerField(null=True, blank=True)
    user_agent_hash = models.CharField(max_length=32, null=True, blank=True)
    referer_domain = models.CharField(max_length=190, null=True, blank=True)
    device_class = models.CharField(max_length=12, choices=DeviceClass.choices, default=DeviceClass.UNKNOWN)
    is_bot = models.BooleanField(default=False)
    status = models.PositiveSmallIntegerField(default=302)
    scan_id = models.CharField(max_length=32)
    source = models.CharField(max_length=16, choices=ScanSource.choices, default=ScanSource.STREAM)

    class Meta:
        managed = False
        db_table = "analytics_scanevent"
        verbose_name = "scan"
        verbose_name_plural = "scans"
        default_related_name = "scanevents"

    def __str__(self) -> str:
        return f"scan {self.scan_id} qr={self.qr_id} {self.country_code or '?'}"


class QrDailyStats(models.Model):
    """Agrégat `(qr, jour, pays)`. C'est la table que le tableau de bord lit."""

    qr_id = models.BigIntegerField("QR", db_index=True)
    day = models.DateField(db_index=True)
    country_code = models.CharField(max_length=2, blank=True, default="")
    scans = models.PositiveIntegerField(default=0)
    unique_visitors = models.PositiveIntegerField(default=0, help_text="Approximation HyperLogLog.")
    mobile = models.PositiveIntegerField(default=0)
    desktop = models.PositiveIntegerField(default=0)
    tablet = models.PositiveIntegerField(default=0)
    bot_blocked = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_qrdailystats"
        verbose_name = "statistique quotidienne"
        verbose_name_plural = "statistiques quotidiennes"
        constraints = [
            models.UniqueConstraint(fields=["qr_id", "day", "country_code"], name="qrdaily_qr_day_country_uniq")
        ]
        indexes = [models.Index(fields=["qr_id", "-day", "country_code"], name="qrdaily_read_idx")]

    def __str__(self) -> str:
        return f"{self.qr_id} {self.day} {self.country_code or 'monde'} = {self.scans}"


class PlatformDailyStats(models.Model):
    # Totaux plateforme d'une journee : la seule lecture que le back-office a le droit de faire.
    #
    # Pourquoi une table et pas un SUM() sur analytics_qrdailystats : a 1 M d'utilisateurs, une ligne par
    # (QR, jour, pays) fait des dizaines de millions de lignes, et un agregat de 90 jours sur cette table
    # se paierait en secondes par rafraichissement de page — sur un dashboard d'administration, qui plus
    # est sans cache. Ici une ligne par jour : la requete du graphique n'a plus de raison de croitre avec
    # le nombre de clients. Alimentee par `aggregates.aggreger_plateforme()` a chaque reconstruction.
    day = models.DateField("jour", unique=True, db_index=True)
    scans = models.BigIntegerField(default=0)
    visiteurs_uniques = models.BigIntegerField(default=0)
    mobile = models.BigIntegerField(default=0)
    desktop = models.BigIntegerField(default=0)
    tablette = models.BigIntegerField(default=0)
    bots_bloques = models.BigIntegerField(default=0)
    qr_uses_statiques = models.BigIntegerField(default=0)
    qr_uses_dynamiques = models.BigIntegerField(default=0)
    # Scans portés par des QR fabriques dans l'espace admin : ce volume-la n'est pas un revenu.
    qr_uses_admin = models.BigIntegerField(default=0)
    qr_actifs = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "analytics_platformdailystats"
        verbose_name = "statistique quotidienne plateforme"
        verbose_name_plural = "statistiques quotidiennes plateforme"
        ordering = ("-day",)
        permissions = [("voir_analytique_plateforme", "Peut consulter l'analytique globale des scans")]

    def __str__(self) -> str:
        return f"{self.day.isoformat()} : {self.scans} scans"
