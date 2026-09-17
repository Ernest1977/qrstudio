"""`django-admin` : la page que vous avez demandée, durcie plutôt que laissé par défaut."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import EmailVerificationCode, User


@admin.register(User)
class StudioUserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "plan", "is_email_verified", "qr_count", "is_active", "is_suspended", "date_joined")
    list_filter = ("plan", "is_email_verified", "is_active", "is_suspended", "consent_tracking_at")
    search_fields = ("email", "qr__slug", "qr__label")
    readonly_fields = ("last_login", "date_joined", "consent_tracking_at", "consent_ip_hash", "json_dump")
    actions = ("resend_verification", "suspend_with_reason", "reactivate", "export_csv")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Profils"), {"fields": ("locale", "timezone_name", "plan", "plan_until")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Statut"), {"fields": ("is_suspended", "suspension_reason", "is_email_verified")}),
        (_("Mesure & consentement"), {"fields": ("consent_tracking_at", "consent_ip_hash", "marketing_opt_in")}),
        (_("Dates"), {"fields": ("last_login", "date_joined"), "classes": ("collapse",)}),
        (_("Technique"), {"fields": ("json_dump",), "classes": ("collapse",)}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "plan"),
            },
        ),
    )

    @admin.display(description="QR")
    def qr_count(self, obj) -> str:
        return str(obj.qrcodes.count())

    @admin.display(description="JSON")
    def json_dump(self, obj) -> str:
        from django.core import serializers

        return serializers.serialize("json", [obj])

    @admin.action(description="Renvoyer le code de confirmation")
    def resend_verification(self, request, queryset):
        from apps.accounts.models import EmailVerificationCode
        from apps.accounts.services import send_verification_email

        sent = 0
        for user in queryset.filter(is_email_verified=False):
            try:
                send_verification_email(user, EmailVerificationCode.issue(user))
                sent += 1
            except Exception as exc:  # noqa: BLE001 - l'admin doit finir, même sur SMTP en panne
                self.message_user(request, f"{user.email} : échec ({exc})", level="WARNING")
        self.message_user(request, f"{sent} e-mail(s) envoyé(s).")

    @admin.action(description="Suspendre le compte (avec motif)")
    def suspend_with_reason(self, request, queryset):
        from django import forms
        from django.contrib import messages

        class ReasonForm(forms.Form):
            reason = forms.CharField(widget=forms.Textarea, required=True, label="Motif (visible par le client)")

        if "apply" not in request.POST:
            form = ReasonForm()
            context = {
                "title": "Motif de suspension",
                "opts": self.model._meta,
                # Litteral volontaire : `admin.helpers` n'est pas couvert par les types de Django, et
                # copier la constante pour la lire une fois vaut mieux qu'un import non typé.
                "action_checkbox_name": "_selected_action",
                "queryset": queryset,
                "form": form,
                "action": "suspend_with_reason",
            }
            return render_to_response_admin(request, context)
        form = ReasonForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Un motif est obligatoire.")
            return None
        count = queryset.update(is_suspended=True, suspension_reason=form.cleaned_data["reason"])
        messages.success(request, f"{count} compte(s) suspendu(s).")
        return None

    @admin.action(description="Réactiver le compte")
    def reactivate(self, request, queryset):
        count = queryset.update(is_suspended=False, is_active=True, suspension_reason="")
        self.message_user(request, f"{count} compte(s) réactivé(s).")

    @admin.action(description="Exporter la sélection (CSV, asynchrone)")
    def export_csv(self, request, queryset):
        from apps.common.export import enqueue_csv_export

        token = enqueue_csv_export(
            model_label=self.model._meta.label,
            queryset_pks=list(queryset.values_list("id", flat=True)),
            actor=request.user,
        )
        self.message_user(request, f"Export demandé ({token}) — le lien de téléchargement arrivera par e-mail.")


def render_to_response_admin(request, context):
    """Repique le gabarit d'action d'admin ; évite de réincrire un formulaire d'admin complet."""
    from django.shortcuts import render

    return render(request, "admin/confirm_reason.html", context)


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires", "attempts", "locked_until", "consumed_at")
    list_filter = ("consumed_at",)
    search_fields = ("user__email",)
    readonly_fields = [f.name for f in EmailVerificationCode._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Expire le")
    def expires(self, obj):
        return obj.expires_at
