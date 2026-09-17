from django.urls import path

from apps.billing import views

app_name = "billing"

urlpatterns = [
    path("checkout", views.CheckoutView.as_view(), name="checkout"),
    path("portail", views.PortailView.as_view(), name="portail"),
    path("etat", views.EtatView.as_view(), name="etat"),
    path("webhook/stripe", views.WebhookStripeView.as_view(), name="webhook-stripe"),
    path("mobile/demande", views.MobileDemandeView.as_view(), name="mobile-demande"),
    # `callback` **avant** `<str:reference>` : sinon `/mobile/callback` est capté par le motif capturant,
    # qui n'accepte que GET — et l'agrégateur reçoit un 405 sur sa notification de paiement confirmé.
    path("mobile/callback", views.MobileCallbackView.as_view(), name="mobile-callback"),
    path("mobile/<str:reference>", views.MobileEtatView.as_view(), name="mobile-etat"),
]
