from django.urls import path

from apps.common import views_backoffice

app_name = "backoffice"

urlpatterns = [
    path("", views_backoffice.dashboard, name="dashboard"),
    path("queue/", views_backoffice.queue_status, name="queue"),
    # Analytique des scans : la page globale, puis le drill-down par QR. Le `<int:pk>` n'est jamais
    # interpolate dans du SQL — `get_object_or_404` s'en charge, et un slug alphanumerique n'y suffirait pas.
    path("analytique/", views_backoffice.analytique, name="analytique"),
    path("analytique/qr/<int:pk>/", views_backoffice.analytique_qr, name="analytique_qr"),
    path("analytique/recalcul/", views_backoffice.recalcul, name="recalcul"),
    # Creation gratuite : le seul chemin ou `origine = "admin"` est ecrit. Suppression logique aussi, pour
    # que l'espace admin ne puisse pas faire disparaitre une ligne d'audit avec un `delete()` sauvage.
    path("qr/creer/", views_backoffice.qr_creer, name="qr_creer"),
    path("qr/<int:pk>/supprimer/", views_backoffice.qr_supprimer, name="qr_supprimer"),
    path("abonnements/", views_backoffice.abonnements, name="abonnements"),
]
