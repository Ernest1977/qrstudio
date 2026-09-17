"""Routage racine.

Trois familles de URLs, volontairement séparées parce qu'elles n'ont pas les mêmes contraintes :

* `/r/<slug>`  : le chemin **chaud**, public, sans CSRF, sans session, lu par des téléphone
                 dans un tunnel de parking. Il ne doit jamais dépendre du reste.
* `/api/v1/`   : l'API du front React (cookies de session + CSRF, même domaine).
* `/manage-9f2/` + `/manage/` : l'admin Django et le back-office métier.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.common import csp

admin.site.site_header = "QR Studio — administration"
admin.site.site_title = "QR Studio"
admin.site.index_title = "Exploitation"


def healthz(request):
    """Sonde pour l'équilibreur / le monitoring Hostinger : vert uniquement si la base répond.

    Volontairement sans détail : un `/healthz` qui énumère les tables est une aide à l'attaque.
    """
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_ok = True
    except Exception:  # noqa: BLE001 - une sonde ne lève jamais
        db_ok = False
    return JsonResponse(
        {"status": "ok" if db_ok else "degraded", "cache": bool(settings.CACHES)},
        status=200 if db_ok else 503,
    )


urlpatterns = [
    path("r/", include("apps.redirect.urls")),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/account/", include("apps.accounts.urls_rgpd")),  # export + effacement (RGPD)
    path("api/v1/", include("apps.qr.urls")),
    path("api/v1/billing/", include("apps.billing.urls")),  # paiement, webhooks, mobile
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="openapi-schema"),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(url_name="openapi-schema"),
        name="openapi-docs",
    ),
    path("accounts/", include("allauth.urls")),
    path("manage/", include("apps.common.urls_backoffice")),
    path(settings.ADMIN_URL, admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    # Point de chute des rapports CSP. Public et sans session : c'est le navigateur qui le poste, au
    # moment ou la page vient d'etre chargee — le lui interdire par CSRF viderait la politique de sa
    # seule valeur de mesure. La vue ne fait que journaliser (voir `apps.common.csp.signaler_violation`).
    path("csp-violation/", csp.signaler_violation, name="csp-violation"),
]

if settings.DEBUG:
    try:
        import debug_toolbar  # noqa: F401

        urlpatterns = [path("__debug__/", include("debug_toolbar.urls")), *urlpatterns]
    except ImportError:
        pass
