"""Sérialiseurs d'authentification.

Règle anti-énumération : les réponses ne révèlent jamais si un compte existe (mot de passe oublié,
inscription d'un e-mail déjà pris → même message, même code 202).
"""

from __future__ import annotations

from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import serializers

from apps.accounts import services
from apps.accounts.models import EmailVerificationCode, User
from apps.common.exceptions import ApiError


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(write_only=True)
    password = serializers.CharField(write_only=True, min_length=12, max_length=256, style={"input_type": "password"})
    consent_tracking = serializers.BooleanField(required=False, write_only=True)
    marketing_opt_in = serializers.BooleanField(required=False, write_only=True)
    accept_terms = serializers.BooleanField(write_only=True)

    def validate_accept_terms(self, value):
        if value is not True:
            raise serializers.ValidationError("Les conditions d'utilisation doivent être acceptées.")
        return value

    def validate_password(self, value):
        try:
            services.check_password_strength(value)
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "messages", [str(exc)])) from exc
        return value

    def create(self, validated_data):
        email = validated_data["email"].strip().lower()
        user = User.objects.create_user(
            email=email,
            password=validated_data["password"],
            marketing_opt_in=validated_data.get("marketing_opt_in", False),
            consent_tracking_at=timezone.now() if validated_data.get("consent_tracking") else None,
            consent_ip_hash=services.consent_fingerprint(self.context.get("client_ip")),
        )
        return user


class VerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=6, max_length=6)

    def validate(self, attrs):
        user = User.objects.filter(email__iexact=attrs["email"]).first()
        if user is None:
            raise ApiError("invalid_code", "Code inconnu ou expiré.")
        pending = (
            EmailVerificationCode.objects.filter(user=user, consumed_at__isnull=True).order_by("-created_at").first()
        )
        if pending is None:
            raise ApiError("no_pending_code", "Aucun code en attente. Demandez-en un nouveau.")
        if pending.is_locked:
            # `is_locked` implique `locked_until` non nul, mais ne pas le verifier rendrait le message
            # dependant d'une invariant du modele : sur une donnee importee a la main, on tombe sur le
            # plafond par defaut plutot que sur un TypeError en pleine tentative de connexion.
            jusqua = pending.locked_until
            minutes = (
                max(1, int((jusqua - timezone.now()).total_seconds() // 60))
                if jusqua
                else int(EmailVerificationCode.LOCKOUT.total_seconds() // 60)
            )
            raise ApiError("locked", f"Trop de tentatives. Réessayez dans {minutes} minute(s).", status_code=429)
        if not pending.matches(attrs["code"]):
            raise ApiError(
                "invalid_code",
                f"Code incorrect. {pending.attempts_left} tentative(s) restante(s).",
                details={"attempts_left": pending.attempts_left},
            )
        pending.mark_verified()
        attrs["user"] = user
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, max_length=256, style={"input_type": "password"})

    def validate(self, attrs):
        request = self.context["request"]
        user = authenticate(request, username=attrs["email"].strip().lower(), password=attrs["password"])
        if user is None:
            raise ApiError("invalid_credentials", "E-mail ou mot de passe incorrect.", status_code=401)
        if user.is_blocked:
            raise ApiError("account_suspended", "Ce compte est suspendu.", status_code=403)
        attrs["user"] = user
        return attrs


class UserSerializer(serializers.ModelSerializer):
    """Auto-publication du compte : `username` est un détail de modèle, on ne l'expose pas."""

    exonere_de_facturation = serializers.SerializerMethodField()
    quota = serializers.SerializerMethodField()
    licence = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "is_email_verified",
            "plan",
            "plan_until",
            "locale",
            "timezone_name",
            "consent_tracking_at",
            "exonere_de_facturation",
            "marketing_opt_in",
            "has_mfa",
            "date_joined",
            "quota",
            "licence",
        ]
        read_only_fields = fields

    has_mfa = serializers.SerializerMethodField()

    def get_has_mfa(self, obj) -> bool:
        from apps.common.staff_access import has_totp

        return has_totp(obj)

    def get_licence(self, obj) -> dict:
        from apps.accounts import plans

        donnees = plans.tableau_pour_api(obj.plan_effectif)
        # Le detail du palier *actuel* suffit au front pour verrouiller une tuile ; la liste complete
        # des paliers est servie par /api/v1/config (route publique, avant meme la connexion).
        donnees.pop("paliers", None)
        return donnees

    def get_exonere_de_facturation(self, obj) -> bool:
        """`True` = la grille tarifaire ne s'applique pas a ce compte (super admin, staff habilite).

        Le front s'en sert pour n'afficher aucun bloc de paiement : ce n'est pas au client de deviner
        qu'il est exonere, et lui vendre un abonnement qu'il ne paiera pas est une faute de produit.
        """
        from apps.accounts.exemption import exonere_de_facturation

        return exonere_de_facturation(obj)

    def get_quota(self, obj) -> dict:
        from apps.qr.quota import dynamic_usage

        used, limit = dynamic_usage(obj)
        return {"dynamic_used": used, "dynamic_limit": limit, "window_days": 30}


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(write_only=True)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password1 = serializers.CharField(write_only=True, min_length=12)
    new_password2 = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password1"] != attrs["new_password2"]:
            raise serializers.ValidationError("Les deux mots de passe diffèrent.")
        try:
            services.check_password_strength(attrs["new_password1"])
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "messages", [str(exc)])) from exc
        return attrs


class LicenceSerializer(serializers.Serializer):
    """Ce que le front doit savoir pour afficher, verrouiller ou proposer une montee de palier.

    Les prix sont servis par le serveur et jamais codés en dur cote client : une grille tarifaire
    change plus souvent qu'une version du front, et un « 2,99 € » ecrit dans le React est deja faux.
    """

    code = serializers.CharField(source="plan_effectif")
    nom = serializers.SerializerMethodField()
    prix_centimes = serializers.SerializerMethodField()
    prix_eur = serializers.SerializerMethodField()
    caracteristiques = serializers.SerializerMethodField()
    limites = serializers.DictField(read_only=True)
    expires_le = serializers.DateTimeField(source="plan_until", read_only=True, allow_null=True)

    def get_nom(self, obj):
        from apps.accounts import plans

        return plans.palier(obj.plan_effectif).nom

    def get_prix_centimes(self, obj):
        from apps.accounts import plans

        return plans.palier(obj.plan_effectif).prix_centimes

    def get_prix_eur(self, obj):
        from apps.accounts import plans

        return plans.palier(obj.plan_effectif).prix_eur

    def get_caracteristiques(self, obj):
        from apps.accounts import plans

        return sorted(plans.caracteristiques(obj.plan_effectif))


class MeUpdateSerializer(serializers.Serializer):
    """Champs modifiables par l'utilisateur depuis son compte — liste courte et fermee.

    `MeView` refuse tout le reste (le plan, les quotas, `is_staff`) ; sans serialiseur explicite, la
    seule trace de cette limite etait un dictionnaire `allowed` dans la vue, et le schema OpenAPI ne
    disait rien a la personne qui genere le client. L'input porte la cle `timezone` (celle du front),
    la modele s'appelle `timezone_name`.
    """

    locale = serializers.ChoiceField(required=False, allow_null=True, choices=["fr", "en", "it"])
    timezone = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=64)
    marketing_opt_in = serializers.BooleanField(required=False, allow_null=True)
