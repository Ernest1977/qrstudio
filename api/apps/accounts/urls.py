from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    # Publique, appelee a chaque demarrage du front : la grille tarifaire et l'etat des fournisseurs.
    path("config", views.ClientConfigView.as_view(), name="client-config"),
    path("register", views.RegisterView.as_view(), name="register"),
    path("verify", views.VerifyView.as_view(), name="verify"),
    path("verify/resend", views.ResendCodeView.as_view(), name="verify-resend"),
    path("login", views.LoginView.as_view(), name="login"),
    path("logout", views.LogoutView.as_view(), name="logout"),
    path("me", views.MeView.as_view(), name="me"),
    path("consent", views.ConsentView.as_view(), name="consent"),
    path("password/reset", views.PasswordResetRequestView.as_view(), name="password-reset"),
    path("password/reset/confirm", views.PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("google/url", views.SocialGoogleUrlView.as_view(), name="google-url"),
]
