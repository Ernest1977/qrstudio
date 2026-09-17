"""QR code de l'utilisateur, statique ou dynamique.

Deux règles de conception qui engagent l'avenir :

1. **le slug ne change jamais**. Un QR est imprimé sur un flyer à 800 € : si la modification de la
   destination changeait l'URL courte, le produit ne vaut plus rien. C'est `target_url` qui bouge.
2. **la suppression est logique** (`deleted_at`) : un QR supprimé par erreur reste résolvable tant
   qu'il n'est pas purgé, et l'utilisateur peut le restaurer. La purge définitive est un *autre*
   évènement (RGPD), déclenché explicitement.

Le contenu statique (`payload`) et la destination dynamique (`target_url`) sont deux colonnes
distinctes et non un champ unique : un QR « fichier » doit rester servi même si l'on désactive le
mode dynamique chez un client.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.common.shortid import new_token


class Kind(models.TextChoices):
    STATIC = "static", "Statique (contenu dans le QR)"
    DYNAMIC = "dynamic", "Dynamique (redirigeable)"


class Origine(models.TextChoices):
    """D'où vient le QR — distinction utile, pas décorative.

    Elle permet de dire dans le back-office ce que l'équipe produit fabrique elle-même (espace admin,
    gratuit par nature) de ce que les clients fabriquent (le volume qui porte la grille tarifaire), et
    de vérifier qu'aucune création facturable n'a été détournee par l'espace gratuit.
    """

    CLIENT = "client", "Créé par le client"
    ADMIN = "admin", "Créé depuis l'espace admin"
    IMPORT = "import", "Importé en masse"


class RedirectMode(models.TextChoices):
    FOUND = "302", "302 — temporaire (compté, non mis en cache)"
    PERMANENT = "301", "301 — permanent (les navigateurs se souviennent, on perd le comptage)"
    TEMP = "307", "307 — reteste la méthode"


class QrCode(models.Model):
    ULID_PREFIX = "qr_"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qrcodes",
        verbose_name="propriétaire",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.STATIC, db_index=True)
    # Ou le QR a ete cree. Par defaut `client` : seul l'espace admin ecrit `admin`. Cette valeur sert a
    # separer, dans le back-office, le volume qui porte la grille tarifaire de celui que l'equipe produit
    # fabrique elle-meme gratuitement — et a prouver qu'aucune creation payante n'a ete detournee.
    origine = models.CharField(max_length=16, choices=Origine.choices, default=Origine.CLIENT, db_index=True)
    slug = models.CharField(max_length=32, unique=True, blank=True, editable=False, verbose_name="lien court")

    type_id = models.CharField(max_length=24, default="url", verbose_name="type")
    label = models.CharField(max_length=160, blank=True)
    notes = models.TextField(blank=True)
    payload = models.TextField(blank=True, verbose_name="contenu encodé")
    target_url = models.URLField(max_length=2048, blank=True, verbose_name="URL de destination")
    design = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField("actif", default=True)
    is_public = models.BooleanField("public", default=True)
    redirect_mode = models.CharField(max_length=4, choices=RedirectMode.choices, default=RedirectMode.FOUND)
    utm_mode = models.BooleanField("ajouter les UTM", default=False)

    scan_count_total = models.BigIntegerField("scans (cumul)", default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "QR code"
        verbose_name_plural = "QR codes"
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(kind=Kind.DYNAMIC) | models.Q(target_url__gt=""),
                name="qr_dynamic_requires_target",
            ),
            models.CheckConstraint(condition=models.Q(scan_count_total__gte=0), name="qr_scan_count_positive"),
        ]
        indexes = [
            # La liste du client est la requête n°1 du produit : couvrir (propriétaire, suppression, date).
            models.Index(fields=["owner", "deleted_at", "-created_at"], name="qr_owner_list_idx"),
            models.Index(fields=["kind", "is_active"], name="qr_kind_active_idx"),
            models.Index(fields=["origine", "-created_at"], name="qr_origine_recent_idx"),
        ]
        permissions = [
            # Accordee nommement, jamais heritee de `is_staff` : c'est elle qui rend un compte exonere de
            # facturation (voir `apps/accounts/exemption.py`) et lui ouvre la creation gratuite.
            ("creer_sans_facturation", "Peut creer des QR depuis l'espace admin sans facturation"),
        ]

    def __str__(self) -> str:
        return f"{self.label or self.slug} ({self.kind})"

    # ------------------------------------------------------------- fabrique
    @classmethod
    def allocate_slug(cls, *, length: int | None = None, attempts: int = 8) -> str:
        """Jusqu'à `attempts` tirages avant de laisser remonter l'erreur.

        À 25 M de QR et 8 caractères (≈ 2,18e14 valeurs), la collision est de l'ordre de 10⁻⁴ par
        création : on retente au lieu de faire porter une unique contrainte à l'appelant.
        """
        length = length or (settings.QR or {}).get("SLUG_LENGTH", 8)
        collision = 0
        for _ in range(attempts):
            candidate = new_token(length)
            if not cls.objects.filter(slug=candidate).exists():
                return candidate
            collision += 1
        raise RuntimeError(f"{collision} collisions consécutives sur des slugs de {length} caractères")

    def save(self, *args, **kwargs):
        creating = self.pk is None
        if creating and self.kind == Kind.DYNAMIC and not self.slug:
            self.slug = self.allocate_slug()
        elif creating and not self.slug:
            # Un QR statique n'a pas besoin de slug ; on en pose un quand même pour que
            # `/r/<slug>` réponde proprement (410) plutôt que 404-Nuxt si l'utilisateur bascule plus
            # tard en dynamique. Coût : une colonne remplie.
            self.slug = self.allocate_slug()
        super().save(*args, **kwargs)
        if creating:
            QrVersion.objects.create(qr=self, actor=self.owner, change={"created": True, "kind": self.kind})

    # ------------------------------------------------------------- état
    @property
    def short_url(self) -> str:
        base = (settings.QR or {}).get("SHORT_BASE_URL", "").rstrip("/")
        return f"{base}/r/{self.slug}" if self.slug else ""

    @property
    def is_resolvable(self) -> bool:
        return self.kind == Kind.DYNAMIC and self.is_active and self.deleted_at is None and bool(self.target_url)

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def touch_cache(self) -> None:
        """Invalide l'entrée de redirect : la modification doit être visible en < 100 ms (§11 critère 2)."""
        from apps.qr import cache as qr_cache

        qr_cache.invalidate(self.slug)

    def set_target(self, url: str, *, actor=None, redirect_mode: str | None = None, commit: bool = True) -> QrVersion:
        from apps.qr.validators import validate_target_url

        url = validate_target_url(url)
        change: dict[str, list[str]] = {"target_url": [self.target_url, url]}
        if redirect_mode and redirect_mode != self.redirect_mode:
            # Lu *avant* l'attribution : sinon le journal enregistre la nouvelle valeur deux fois et
            # il devient impossible d'expliquer au client pourquoi son flyer pointe ailleurs.
            change["redirect_mode"] = [self.redirect_mode, redirect_mode]
            self.redirect_mode = redirect_mode
        self.target_url = url
        with transaction.atomic():
            if commit:
                fields = ["target_url", "updated_at"]
                if redirect_mode:
                    fields.insert(1, "redirect_mode")
                self.save(update_fields=fields)
            version = QrVersion.objects.create(qr=self, actor=actor or self.owner, change=change)
        self.touch_cache()
        return version

    def pause(self, *, actor=None) -> None:
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])
        QrVersion.objects.create(qr=self, actor=actor, change={"paused": True})
        self.touch_cache()

    def resume(self, *, actor=None) -> None:
        self.is_active = True
        self.save(update_fields=["is_active", "updated_at"])
        QrVersion.objects.create(qr=self, actor=actor, change={"resumed": True})
        self.touch_cache()

    def soft_delete(self, *, actor=None) -> None:
        self.deleted_at = timezone.now()
        self.is_active = False
        self.save(update_fields=["deleted_at", "is_active", "updated_at"])
        QrVersion.objects.create(qr=self, actor=actor, change={"deleted": True})
        self.touch_cache()

    def restore(self, *, actor=None) -> None:
        self.deleted_at = None
        self.is_active = True
        self.archived_at = None
        self.save(update_fields=["deleted_at", "is_active", "archived_at", "updated_at"])
        QrVersion.objects.create(qr=self, actor=actor, change={"restored": True})
        self.touch_cache()


class RemiseType(models.TextChoices):
    POURCENTAGE = "pourcentage", "Pourcentage"
    MONTANT = "montant", "Montant"
    LIVRAISON = "livraison", "Livraison offerte"
    ACCES = "acces", "Accès offert"


class PromoCode(models.Model):
    """Un code d'offre généré pour *les clients du compte*, avec une date de fin obligatoire.

    Ce n'est pas la facturation de la plateforme : rien ici ne débite qui que ce soit. Le lien avec le
    QR est ce qui rend le code utile — le visiteur qui scanne arrive sur l'offre **tant qu'elle court**,
    et sur un avis « offre terminée » sinon, sans que le commerçant ait à retirer ses flyers.
    """

    REMISES = RemiseType

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="codes_promo", verbose_name="compte"
    )
    qr = models.ForeignKey(
        QrCode,
        on_delete=models.CASCADE,
        related_name="codes_promo",
        null=True,
        blank=True,
        verbose_name="QR associé",
        help_text="L'offre peut exister sans QR (code imprimé à la main) ; avec, la vérification se fait au scan.",
    )
    code = models.CharField(max_length=48, verbose_name="code", help_text="Normalisé : majuscules, sans espace.")
    libelle = models.CharField(max_length=160, blank=True, verbose_name="intitulé affiché")
    remise_type = models.CharField(max_length=12, choices=RemiseType.choices, default=RemiseType.POURCENTAGE)
    remise_valeur = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="valeur de la remise"
    )
    devise = models.CharField(max_length=3, default="EUR", blank=True, verbose_name="devise")
    # Pas de `null=True` : « sans date de fin » n'est pas une option de ce produit, et le champs vide
    # serait la porte de sortie de quelqu'un qui n'a pas lu la phrase précédente.
    expire_le = models.DateTimeField(verbose_name="expire le")
    usages_max = models.PositiveIntegerField(null=True, blank=True, verbose_name="usages maximum")
    usages = models.PositiveIntegerField(default=0, verbose_name="usages constatés")
    actif = models.BooleanField("actif", default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "code promo"
        verbose_name_plural = "codes promo"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["owner", "-created_at"], name="qr_promo_owner_recent"),
            models.Index(fields=["owner", "expire_le"], name="qr_promo_owner_expire"),
            models.Index(fields=["qr", "actif"], name="qr_promo_qr_actif"),
        ]
        constraints = [
            # Deux comptes peuvent légitimement avoir le même code (« BIENVENUE ») ; un compte non.
            models.UniqueConstraint(fields=["owner", "code"], name="qr_promo_code_unique_par_compte")
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.libelle or 'sans intitulé'}"

    def save(self, *args, **kwargs):
        from apps.qr import promo as regles

        self.code = regles.cle_normalisee(self.code)
        # Un code lié à un QR doit invalider l'entrée de cache de ce QR : la redirection lit la validité
        # depuis le cache, et une offre créée après le premier scan resterait invisible sans ce geste.
        deja = None
        if self.pk:
            deja = PromoCode.objects.filter(pk=self.pk).values_list("qr_id", "actif", "expire_le").first()
        super().save(*args, **kwargs)
        if self.qr_id and (deja is None or (deja[1], deja[2]) != (self.actif, self.expire_le) or deja[0] != self.qr_id):
            self.qr.touch_cache()

    def touch_qr_cache(self) -> None:
        """Redessiner l'entree de cache du QR lie : c'est la que la redirection lit la validite de l'offre."""
        if self.qr_id:
            qr = QrCode.objects.filter(pk=self.qr_id).first()
            if qr is not None:
                qr.touch_cache()

    def delete(self, *args, **kwargs):
        # Retirer l'offre doit retirer la verification du chemin de redirection : sinon le QR reste
        # bloque sur une promo qui n'existe plus (et affiche « offre terminee » pour rien).
        qr = self.qr
        resultat = super().delete(*args, **kwargs)
        if qr is not None:
            qr.touch_cache()
        return resultat

    @property
    def est_valide(self) -> bool:
        return self.statut().valide

    def statut(self):
        from apps.qr import promo as regles

        return regles.statut(self)


class QrVersion(models.Model):
    """Historique des modifications. Sans lui, « mon QR est cassé depuis hier » est indiagnosticable."""

    qr = models.ForeignKey(QrCode, on_delete=models.CASCADE, related_name="versions")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    change = models.JSONField(default=dict)

    class Meta:
        verbose_name = "modification"
        verbose_name_plural = "historique des modifications"
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["qr", "-created_at"], name="qrver_qr_recent_idx")]

    def __str__(self) -> str:
        return f"{self.qr_id}@{self.created_at:%Y-%m-%d %H:%M}"


class QrReport(models.Model):
    """Signalement d'une destination illicite — la file de modération du back-office (sprint 5)."""

    class Status(models.TextChoices):
        NEW = "new", "Nouveau"
        REVIEWING = "reviewing", "En cours"
        DISMISSED = "dismissed", "Écarté"
        FROZEN = "frozen", "Gel"

    qr = models.ForeignKey(QrCode, on_delete=models.CASCADE, related_name="reports")
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    reporter_email = models.EmailField(blank=True)
    reporter_ip_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self) -> str:
        return f"signalement {self.status} sur {self.qr_id}"

    class Meta:
        verbose_name = "signalement"
        verbose_name_plural = "signalements"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["qr", "reporter_ip_hash"], condition=models.Q(status="new"), name="report_once_per_ip"
            )
        ]
