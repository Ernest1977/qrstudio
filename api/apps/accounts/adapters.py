"""Adaptateur allauth : ce qui doit rester vrai quel que soit le chemin d'entrée (Google ou non)."""

from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter


class StudioAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        """Ouvert, mais le quota d'inscriptions par IP est traité par le throttle de la vue register.

        allauth ne connaît pas notre `EmailVerificationCode` : on laisse sa création à la vue API
        pour qu'il n'existe **qu'un** chemin qui émet un code.
        """
        return True

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        if not user.username:
            user.username = user.email
        if user.consent_tracking_at is None:
            # Un compte créé via le formulaire web n'a pas coché la case : on ne présume rien (RGPD).
            user.is_email_verified = False
        if commit:
            user.save()
        return user

    def pre_login(self, *args, **kwargs):
        # Signature allauth : pre_login(request, user, ...) — on lit les deux formes possibles, car
        # allauth a basculé ses arguments en mots-clés en 2024 et un déploiement mixte doit rester lisible.
        request = args[0] if args else kwargs.get("request")
        user = kwargs.get("user") or (args[1] if len(args) > 1 else None)
        del request
        if user is not None and getattr(user, "is_blocked", False):
            from allauth.account.adapter import ImmediateHttpResponse
            from django.http import JsonResponse

            raise ImmediateHttpResponse(
                JsonResponse(
                    {"error": {"code": "account_suspended", "message": "Ce compte est suspendu."}},
                    status=403,
                )
            )
        return super().pre_login(*args, **kwargs)

    def get_login_redirect_url(self, request):
        return request.GET.get("next") or "/dashboard/"

    def send_confirmation_mail(self, emailconfirmation, signup):  # pragma: no cover - on n'utilise pas
        """Voulu : on ne double pas notre code à 6 chiffres avec un lien allauth."""
        return None
