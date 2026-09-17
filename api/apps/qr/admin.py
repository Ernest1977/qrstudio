from django.contrib import admin
from django.utils.html import format_html

from apps.common.admin_mixins import ExplicitUrlSchemeAdminMixin
from apps.qr.models import PromoCode, QrCode, QrReport, QrVersion


class PromoCodeInline(admin.TabularInline):
    """Les offres portees par ce QR, dans la fiche du QR : c'est la qu'on les cherche d'abord.

    `code` et `usages` sont en lecture seule : le premier est genere, le second est resserré par
    `manage.py sync_promo_usages` depuis Redis. Les rendre editables donnerait un moyen de „corriger“ un
    compteur sans que le compteur chaud ne le sache — deux source de verite, et c'est celle de la caisse
    qui gagnerait.
    """

    model = PromoCode
    fk_name = "qr"
    extra = 0
    fields = ("code", "libelle", "remise_type", "remise_valeur", "devise", "expire_le", "usages_max", "usages", "actif")
    readonly_fields = ("code", "usages")


class QrVersionInline(admin.TabularInline):
    model = QrVersion
    extra = 0
    can_delete = False
    fields = ("created_at", "actor", "change")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(QrCode)
class QrCodeAdmin(ExplicitUrlSchemeAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "slug",
        "owner",
        "kind",
        "type_id",
        "label",
        "active_toggle",
        "scan_count_total",
        "created_at",
    )
    # `owner__plan` : traversal de FK vers un champ à choices -> `FieldListFilter` simple.
    # `(…, RelatedOnlyFieldListFilter)` n'est valable que pour une relation et levait un 500.
    list_filter = ("kind", "type_id", "is_active", "owner__plan")
    search_fields = ("slug", "label", "owner__email", "target_url")
    autocomplete_fields = ("owner",)
    inlines = [QrVersionInline, PromoCodeInline]
    readonly_fields = ("slug", "scan_count_total", "created_at", "updated_at", "short_url_display")
    actions = ("freeze_qrs", "unfreeze_qrs", "rebuild_stats")
    date_hierarchy = "created_at"

    @admin.display(description="Actif")
    def active_toggle(self, obj):
        color = "#16a34a" if obj.is_active else "#b45309"
        return format_html('<span style="color:{}">{}</span>', color, "oui" if obj.is_active else "pause")

    @admin.display(description="Lien court")
    def short_url_display(self, obj):
        if not obj.slug:
            return "—"
        return format_html('<a href="{0}" target="_blank" rel="noopener">{0}</a>', obj.short_url)

    @admin.action(description="Geler (le scan renvoie 410)")
    def freeze_qrs(self, request, queryset):
        count = 0
        for qr in queryset:
            qr.pause(actor=request.user)
            count += 1
        self.message_user(request, f"{count} QR gelé(s).")

    @admin.action(description="Rendre à nouveau actif")
    def unfreeze_qrs(self, request, queryset):
        count = 0
        for qr in queryset:
            qr.resume(actor=request.user)
            count += 1
        self.message_user(request, f"{count} QR réactivé(s).")

    @admin.action(description="Recalculer les agrégats de scan")
    def rebuild_stats(self, request, queryset):
        from apps.analytics.tasks import rebuild_daily_stats

        for qr in queryset[:200]:
            rebuild_daily_stats.delay(qr_id=qr.pk)
        self.message_user(request, "Recalculs demandés (jusqu'à 200 QR).")


@admin.register(QrReport)
class QrReportAdmin(admin.ModelAdmin):
    list_display = ("created_at", "qr", "status", "reason", "reviewed_by")
    list_filter = ("status", "created_at")
    search_fields = ("qr__slug", "reason")
    actions = ("mark_reviewing", "mark_dismissed", "mark_frozen")

    @admin.action(description="Marquer « en cours de review »")
    def mark_reviewing(self, request, queryset):
        queryset.update(status="reviewing")

    @admin.action(description="Écarter le signalement")
    def mark_dismissed(self, request, queryset):
        queryset.update(status="dismissed", reviewed_by=request.user)

    @admin.action(description="Geler le QR et écarter le risque")
    def mark_frozen(self, request, queryset):
        for report in queryset.select_related("qr"):
            report.qr.pause(actor=request.user)  # gel = `is_active=False` : le scan répondra 410
            report.status = "frozen"
            report.reviewed_by = request.user
            report.save(update_fields=["status", "reviewed_by"])


@admin.register(QrVersion)
class QrVersionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "qr", "actor", "summary")
    search_fields = ("qr__slug", "actor__email")
    readonly_fields = [f.name for f in QrVersion._meta.fields]

    @admin.display(description="Changement")
    def summary(self, obj):
        return ", ".join(obj.change.keys()) if isinstance(obj.change, dict) else str(obj.change)[:80]


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    """La file des offres : « mon code ne marche plus » se répond ici, en trois clics.

    `usages` est en lecture seule pour la meme raison que dans l'inline, et `code` aussi parce qu'il est
    genere : un agent qui le reecrit casse le lien entre le flyer imprime et la ligne en base.
    """

    list_display = ("code", "owner", "qr", "libelle", "remise_affichee", "expire_le", "usages", "actif")
    list_filter = ("actif", "remise_type", "owner__plan")
    search_fields = ("code", "libelle", "owner__email", "qr__slug", "qr__label")
    autocomplete_fields = ("owner", "qr")
    readonly_fields = ("code", "usages", "created_at", "statut_affiche")
    date_hierarchy = "expire_le"
    actions = ("clore_offres", "reactiver_offres")
    ordering = ("-created_at",)

    @admin.display(description="Remise")
    def remise_affichee(self, obj):
        # La remise s'affiche telle qu'elle est ecrite, pas telle qu'elle serait appliquee : le verdict,
        # lui, est dans `statut_affiche` — deux colonnes, deux questions differentes.
        if obj.remise_type == "pourcentage" and obj.remise_valeur is not None:
            return f"-{obj.remise_valeur} %"
        if obj.remise_type == "montant" and obj.remise_valeur is not None:
            return f"-{obj.remise_valeur} {obj.devise}"
        return obj.get_remise_type_display() or "—"

    @admin.display(description="Verdict (regle du scan)")
    def statut_affiche(self, obj):
        """Ce que le visiteur verra **maintenant**, calculé par la même fonction que la redirection.

        Afficher un booléen `actif` ne suffit pas : une offre active mais dont la date est passée ou le
        quota atteint est close pour le scanner, et c'est la réponse à la question de l'agent.
        """
        from apps.qr import promo as regles

        verdict = regles.statut(obj)
        return verdict.message if not verdict.valide else f"Valide — {verdict.usages_restants or '∞'} usage(s) restants"

    @admin.action(description="Clôturer (le scan affichera « offre terminée »)")
    def clore_offres(self, request, queryset):
        n = 0
        for ligne in queryset:
            ligne.actif = False
            ligne.save(update_fields=["actif"])  # `save()` invalide le cache du QR lie
            n += 1
        self.message_user(request, f"{n} offre(s) close(s) ; les QR liés ne vérifieront plus de code.")

    @admin.action(description="Réactiver jusqu'à la date de fin")
    def reactiver_offres(self, request, queryset):
        n = 0
        for ligne in queryset:
            ligne.actif = True
            ligne.save(update_fields=["actif"])
            n += 1
        self.message_user(request, f"{n} offre(s) réactivée(s).")
