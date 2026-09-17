"""Vues d'authentification de l'API.

Modèle de session : **cookie de session + CSRF**, pas de JWT (cf. ARCHITECTURE.md §10). Le front lit
`csrftoken` (cookie non-HttpOnly en prod, c'est le compromis de ce mode) et le renvoie en
`X-CSRFToken`. Cela donne la déconnexion serveur, la révocation immédiate et zéro jeton volable.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.middleware.csrf import rotate_token
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import serializers as sz
from apps.accounts import services
from apps.accounts.models import EmailVerificationCode, User
from apps.common import throttling
from apps.common.exceptions import ApiError

logger = logging.getLogger(__name__)


def _client_ip(request) -> str | None:
    return getattr(request, "client_ip", None)


class RegisterView(APIView):
    """`POST /api/v1/auth/register` — crée le compte, envoie le code, ne connecte pas."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [throttling.RegisterThrottle]
    throttle_scope = "register"

    @extend_schema(request=sz.RegisterSerializer, responses=None)
    def post(self, request):
        if not settings.ALLOW_REGISTRATION:
            # Le drapeau est annonce par `/config` (le front masque le formulaire) ET verifie ici :
            # une fermeture de registre pendant un incident ne doit dependre d'aucun cote client.
            raise ApiError(
                "registration_closed",
                "Les inscriptions sont temporairement fermees sur cette instance.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        serializer = sz.RegisterSerializer(data=request.data, context={"client_ip": _client_ip(request)})
        serializer.is_valid(raise_exception=True)
        # Anti-énumération : les trois issues possibles (créé, déjà pris, course gagnée par un autre)
        # renvoient **le même corps**. Une réponse d'erreur, même en 202, se distinguait par la forme
        # du JSON — c'est exactement ce que le test `test_email_deja_pris...` a attrapé.
        if User.objects.filter(email__iexact=serializer.validated_data["email"]).exists():
            return self._neutral()
        try:
            user = serializer.save()
        except Exception as exc:
            if "unique" in str(exc).lower() or "constraint" in str(exc).lower():
                return self._neutral()
            raise
        code = EmailVerificationCode.issue(user)
        # Un SMTP en panne n'annule pas l'inscription : le compte existe, le code est en base, un
        # renvoi existe (`verify/resend`). Répondre 500 ici ferait croire à l'utilisateur qu'il doit
        # recréer un compte — et créerait un doublon en base.
        try:
            services.send_verification_email(user, code)
        except Exception as exc:  # noqa: BLE001
            logger.error("email de verification en echec user_id=%s: %s", user.pk, exc)
        return self._neutral()

    def _neutral(self):
        return Response(
            {
                "status": "email_envoye",
                "email": self.request.data.get("email", ""),
                "requires_verification": True,
                "message": "Si cette adresse est libre, un code de confirmation vient d'être envoyé.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ResendCodeView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [throttling.RegisterThrottle]
    throttle_scope = "register"

    @extend_schema(request=None, responses=None)
    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        user = User.objects.filter(email=email).first()
        if user and not user.is_email_verified:
            code = EmailVerificationCode.issue(user)
            try:
                services.send_verification_email(user, code)
            except Exception as exc:  # noqa: BLE001
                logger.error("renvoi du code en echec: %s", exc)
        # Réponse neutre dans tous les cas (anti-énumération).
        return Response({"message": "Si un code est en attente, il vient d'être renvoyé."}, status=202)


class VerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [throttling.VerifyThrottle]
    throttle_scope = "verify"

    @extend_schema(request=sz.VerifySerializer, responses=None)
    def post(self, request):
        serializer = sz.VerifySerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        return Response({"status": "verifie", "email": user.email})


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [throttling.LoginThrottle]
    throttle_scope = "login"

    @extend_schema(request=sz.LoginSerializer, responses=None)
    def post(self, request):
        serializer = sz.LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        auth_login(request, user)
        rotate_token(request)  # la session vient de changer : l'ancien jeton CSRF ne vaut plus
        services.touch_last_login(user)
        logger.info("login user_id=%s ip_hash=%s", user.pk, services.consent_fingerprint(_client_ip(request)))
        return Response(
            {
                "user": sz.UserSerializer(user).data,
                "csrf_token": request.META.get("CSRF_COOKIE", ""),
                "next": request.query_params.get("next") or "/dashboard/",
            }
        )


class LogoutView(APIView):
    @extend_schema(request=None, responses=None)
    def post(self, request):
        auth_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    # `MeView` n'a pas de `serializer_class` : il lit un sous-ensemble de champs a la main pour que le
    # front puisse envoyer son objet de reglages entier sans qu'on enregistre ce qui ne lui appartient
    # pas. Sans annotation, drf-spectacular echoue a deviner le corps — et `check --deploy` (qui refuse
    # tout avertissement en CI) devient rouge sur une route pourtant correcte.
    get=extend_schema(responses={200: sz.UserSerializer}),
    patch=extend_schema(request=sz.MeUpdateSerializer, responses={200: sz.UserSerializer}),
)
class MeView(APIView):
    def get(self, request):
        return Response(sz.UserSerializer(request.user).data)

    def patch(self, request):
        allowed = {
            "locale": request.data.get("locale"),
            "timezone_name": request.data.get("timezone"),
            "marketing_opt_in": request.data.get("marketing_opt_in"),
        }
        user = request.user
        if allowed["locale"] is not None:
            if allowed["locale"] not in {"fr", "en", "it"}:
                raise ApiError("invalid_locale", "Langue non supportée.")
            user.locale = allowed["locale"]
        if allowed["timezone_name"]:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(allowed["timezone_name"])
            except (ZoneInfoNotFoundError, ValueError):
                raise ApiError("invalid_timezone", "Fuseau inconnu.") from None
            user.timezone_name = allowed["timezone_name"]
        if allowed["marketing_opt_in"] is not None:
            user.marketing_opt_in = bool(allowed["marketing_opt_in"])
        user.save()
        return Response(sz.UserSerializer(user).data)


class ConsentView(APIView):
    """Pose ou retire le consentement à la mesure d'audience (base légale des stats par pays)."""

    @extend_schema(request=None, responses=None)
    def post(self, request):
        granted = bool(request.data.get("consent_tracking", False))
        request.user.consent_tracking_at = services.timezone.now() if granted else None
        request.user.consent_ip_hash = services.consent_fingerprint(_client_ip(request) if granted else None)
        request.user.save(update_fields=["consent_tracking_at", "consent_ip_hash"])
        logger.info("consentement user_id=%s valeur=%s", request.user.pk, granted)
        return Response({"consent_tracking_at": request.user.consent_tracking_at})


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [throttling.PasswordResetThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=sz.PasswordResetRequestSerializer, responses=None)
    def post(self, request):
        serializer = sz.PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        user = User.objects.filter(email=email, is_active=True).first()
        if user:
            from django.contrib.auth.tokens import default_token_generator
            from django.utils.http import urlsafe_base64_encode

            uid = urlsafe_base64_encode(str(user.pk).encode())
            services.send_password_reset_email(user, uid, default_token_generator.make_token(user))
        return Response({"message": "Si cette adresse est enregistrée, un e-mail vient d'être envoyé."}, status=202)


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [throttling.PasswordResetThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=sz.PasswordResetConfirmSerializer, responses=None)
    def post(self, request):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_decode

        serializer = sz.PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            uid = urlsafe_base64_decode(data["uid"]).decode()
            user = User.objects.get(pk=uid)
        except (ValueError, TypeError, UnicodeDecodeError, User.DoesNotExist):
            raise ApiError("invalid_token", "Lien de réinitialisation invalide ou expiré.") from None
        if not default_token_generator.check_token(user, data["token"]):
            raise ApiError("invalid_token", "Lien de réinitialisation invalide ou expiré.")
        user.set_password(data["new_password1"])
        user.is_email_verified = True  # prouver la propriété du compte vaut la vérification
        user.save()
        # Les sessions ouvertes ailleurs sont invalidées : un réinitialisation suit souvent un oubli…
        # Les sessions ouvertes ailleurs restent valides : les révoquer par utilisateur demande un
        # passage dans `django_session` (tâche `flush_stale_sessions`, sprint 5), pas un effet de
        # bord de cette vue.
        return Response({"status": "mot_de_passe_renouvelle"}, status=204)


class SocialGoogleUrlView(APIView):
    """Le front redirige vers l'URL renvoyée ; allauth gère l'échange et le callback.

    On ne transmet **jamais** l'`access_token` depuis le navigateur : l'échange code→token se fait
    côté serveur (PKCE + `state`), sinon un jeton volé dans l'onglet vaut un compte.
    """

    permission_classes = [AllowAny]

    @extend_schema(request=None, responses=None)
    def get(self, request):
        if not services.google_actif():
            # 503 et non 404 : la fonction existe, elle n'est pas configurée. Le front cache le bouton
            # d'après `/api/v1/auth/config`, donc ce code n'est atteint que par un appel direct — un
            # message clair évite alors d'ouvrir un ticket « la connexion ne marche pas ».
            # Deux codes, pas un : « rien n'est saisi » et « l'exploitant a coupé » ne se règlent pas
            # de la même façon, et le second ne doit surtout pas finir en ticket chez Google.
            desactive = bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_LOGIN_ENABLED is False)
            raise ApiError(
                "provider_disabled" if desactive else "google_not_configured",
                "La connexion Google est désactivée sur cette instance."
                if desactive
                else "La connexion Google n'est pas configurée sur cet environnement.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        nxt = request.query_params.get("next") or "/dashboard/"
        try:
            from allauth.socialaccount.providers.registry import provider_by_id

            authorize = provider_by_id("google").get_login_url(request, process="login")
        except Exception:  # noqa: BLE001 - on ne casse pas pour un renommage interne à allauth
            from urllib.parse import quote

            authorize = f"/accounts/google/login/?process=login&next={quote(nxt, safe='')}"
        separator = "&" if "?" in authorize else "?"
        return Response(
            {
                "authorize_url": f"{authorize}{separator}next={nxt}",
                "callback_hint": "/accounts/google/login/callback/",
            }
        )


class AccountExportView(APIView):
    """Export RGPD (portabilite) du compte courant, en JSON telechargeable.

    `Content-Disposition: attachment` avec un nom daté : un client qui rassemble ses preuves pour un
    litige a besoin d'un fichier qui se range tout seul, pas de `download.json` sans date.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=None)
    def get(self, request):
        from django.http import JsonResponse

        from apps.accounts import services

        lignes = services.count_export_rows(request.user)
        if lignes > services.EXPORT_LIMITE_LIGNES:
            raise ApiError(
                "export_too_large",
                "Volume d'export trop important pour une reponse synchrone.",
                status_code=413,
                details={"lignes": lignes, "limite": services.EXPORT_LIMITE_LIGNES},
            )
        donnees = services.export_account(request.user)
        reponse = JsonResponse(donnees, json_dumps_params={"ensure_ascii": False, "indent": 2})
        jour = services.timezone.now().date().isoformat()
        reponse["Content-Disposition"] = f'attachment; filename="qrs-export-{jour}.json"'
        return reponse


class AccountDeleteView(APIView):
    """Droit a l'effacement: supprime le compte et tout ce qui en depend, sans trace consultable.

    Re-authentification obligatoire. Un compte lie uniquement a Google n'a pas de mot de passe : on
    lui demande alors de retaper le mot d'ordre, ce qui couvre le cas du poste laisse ouvert sans
    rendre la route attaquable par un simple token vole (le token ne sait pas taper `SUPPRIMER`).
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [
        throttling.LoginThrottle
    ]  # meme plafond que la connexion: on brise aussi un brute-force du mot de passe

    @extend_schema(request=None, responses=None)
    def delete(self, request):
        from apps.accounts import services

        mot_de_passe = request.data.get("password") or ""
        confirmation = str(request.data.get("confirm") or "").strip().upper()
        if request.user.has_usable_password():
            if not request.user.check_password(mot_de_passe):
                raise ApiError("wrong_password", "Mot de passe incorrect.", status_code=403)
        elif confirmation != "SUPPRIMER":
            raise ApiError(
                "confirmation_required",
                "Ce compte est lie a un fournisseur tiers: tapez SUPPRIMER pour confirmer.",
                status_code=400,
            )
        bilan = services.delete_account(request.user, actor=None)
        # La session est detruite *avant* la reponse: un 204 qui laisse un `sessionid` valide ferait
        # tourner le front sur un utilisateur fantome jusqu'au prochain 401.
        auth_logout(request)
        reponse = Response(status=204)
        reponse.headers["X-Deleted"] = ",".join(f"{k}={v}" for k, v in bilan.items() if isinstance(v, int))
        return reponse


class ClientConfigView(APIView):
    """Ce que le front doit savoir **avant** de demander un mot de passe.

    Deux choses, et elles sont publiques parce qu'elles concernent l'offre, pas le client : la grille
    des paliers (prix, ce que chacun ouvre) et l'etat des fournisseurs de connexion. Sans cette route,
    le React devrait coder les prix en dur — un prix qui vit dans deux repos est faux dans l'un des
    deux au premier changement — et afficher un bouton Google meme la ou il n'est pas configure.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [throttling.StatsThrottle]

    @extend_schema(request=None, responses=None)
    def get(self, request):
        from apps.accounts import plans

        return Response(
            {
                "paliers": [
                    {
                        "code": p.code,
                        "nom": p.nom,
                        "prix_centimes": p.prix_centimes,
                        "prix_eur": p.prix_eur,
                        "apporte": sorted(p.apporte),
                        "limites": dict(p.limites),
                    }
                    for p in plans.PALIERS.values()
                ],
                "fournisseurs": {
                    "google": {
                        "actif": services.google_actif(),
                        "raison": None
                        if services.google_actif()
                        else ("desactive" if settings.GOOGLE_CLIENT_ID else "client_id_absent"),
                    }
                },
                "inscription_ouverte": settings.ALLOW_REGISTRATION,
            }
        )
