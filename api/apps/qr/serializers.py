"""Sérialiseurs QR. `design` est une boîte noire venue du front, bornée en taille et en profondeur.

On ne valide pas les couleurs ici : le front les produit, et un QR au fond bleu-ciel invalide se
décodera mal mais reste un choix de l'utilisateur. Ce qui est validé, c'est ce qui nous protège :
longueur du contenu, du design, de l'URL, et la destination.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.qr.models import PromoCode, QrCode, QrVersion
from apps.qr.validators import validate_target_url

MAX_DESIGN_NODES = 400


def _design_size(value) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return 10**9


class QrVersionSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = QrVersion
        fields = ["id", "created_at", "change", "actor_email"]


class QrSerializer(serializers.ModelSerializer):
    short_url = serializers.CharField(read_only=True)
    is_resolvable = serializers.BooleanField(read_only=True)
    versions = serializers.SerializerMethodField()
    target_url = serializers.CharField(required=False, allow_blank=True, max_length=2048)

    class Meta:
        model = QrCode
        fields = [
            "id",
            "slug",
            "kind",
            "type_id",
            "label",
            "notes",
            "payload",
            "target_url",
            "short_url",
            "design",
            "is_active",
            "is_public",
            "is_resolvable",
            "redirect_mode",
            "utm_mode",
            "scan_count_total",
            "created_at",
            "updated_at",
            "archived_at",
            "deleted_at",
            "versions",
        ]
        read_only_fields = ["id", "slug", "short_url", "scan_count_total", "created_at", "updated_at", "deleted_at"]

    def validate_type_id(self, value):
        from apps.qr.types import KNOWN_TYPE_IDS

        if value not in KNOWN_TYPE_IDS:
            raise serializers.ValidationError(f"Type inconnu : {value}.")
        return value

    def validate_payload(self, value):
        limit = (settings.QR or {}).get("MAX_PAYLOAD_BYTES", 2953)
        size = len((value or "").encode("utf-8"))
        if size > limit:
            raise serializers.ValidationError(
                f"Contenu de {size} octets : la capacité maximale d'un QR est de {limit} octets "
                "(version 40, correction L)."
            )
        return value

    def validate_design(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Le design doit être un objet.")
        limit = (settings.QR or {}).get("DESIGN_MAX_BYTES", 16_384)
        size = _design_size(value)
        if size > limit:
            raise serializers.ValidationError(f"Design trop volumineux ({size} octets, max {limit}).")
        if _count_nodes(value) > MAX_DESIGN_NODES:
            raise serializers.ValidationError("Structure de design trop profonde.")
        return value

    def validate(self, attrs):
        instance = self.instance
        kind = attrs.get("kind", getattr(instance, "kind", "static"))
        target = attrs.get("target_url", getattr(instance, "target_url", ""))
        if kind == "dynamic":
            if not (target or "").strip():
                raise serializers.ValidationError({"target_url": "Une destination est requise pour un QR dynamique."})
            attrs["target_url"] = validate_target_url(target)
        elif target:
            attrs["target_url"] = validate_target_url(target)
        return attrs

    def get_versions(self, obj) -> list[dict]:
        if not self.context.get("include_versions"):
            return []
        return QrVersionSerializer(obj.versions.all()[:20], many=True).data


def _count_nodes(value, depth: int = 0) -> int:
    if depth > 12:
        return MAX_DESIGN_NODES + 1
    if isinstance(value, dict):
        return 1 + sum(_count_nodes(v, depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        return 1 + sum(_count_nodes(v, depth + 1) for v in value)
    return 1


class PromoCodeSerializer(serializers.ModelSerializer):
    """Un code d'offre pour les clients du compte. La valeur de la remise depend du type : c'est verifie.

    Le code n'est **pas** saisi par le client, il est genere (`apps.qr.promo.generer_code`) : un code tape
    a la main est un code qui contient deja un `O` lu `0`, ou qui duplique l'offre du concurrent. Le rendre
    en lecture seule n'est pas une privatisation du champ, c'est la fin d'une classe d'incidents.
    """

    statut = serializers.SerializerMethodField()
    lien = serializers.SerializerMethodField()
    expire_bientot = serializers.SerializerMethodField()

    class Meta:
        model = PromoCode
        fields = (
            "id",
            "code",
            "libelle",
            "qr",
            "remise_type",
            "remise_valeur",
            "devise",
            "expire_le",
            "usages_max",
            "usages",
            "actif",
            "notes",
            "created_at",
            "statut",
            "lien",
            "expire_bientot",
        )
        # `code` est en lecture seule par contrat : il est genere (`promo.generer_code`), jamais propose.
        read_only_fields = ("id", "code", "usages", "created_at", "statut", "lien", "expire_bientot")

    def validate_expire_le(self, valeur):
        from django.utils import timezone

        if valeur <= timezone.now():
            raise serializers.ValidationError("La date d'expiration doit etre dans le futur.")
        if valeur > timezone.now() + timedelta(days=730):
            raise serializers.ValidationError("Au-dela de deux ans, ce n'est plus une offre mais un tarif.")
        return valeur

    def validate(self, attrs):
        remise = attrs.get("remise_type") or getattr(self.instance, "remise_type", "")
        valeur = attrs.get("remise_valeur", getattr(self.instance, "remise_valeur", None))
        if remise in {"pourcentage", "montant"}:
            if valeur is None:
                raise serializers.ValidationError(
                    {"remise_valeur": "Une remise en pourcentage ou en montant exige une valeur."}
                )
            if remise == "pourcentage" and not 0 < valeur <= 100:
                raise serializers.ValidationError({"remise_valeur": "Un pourcentage d'offre est entre 0 et 100."})
            if remise == "montant" and valeur <= 0:
                raise serializers.ValidationError({"remise_valeur": "Un montant d'offre doit etre positif."})
        else:
            # `livraison` et `acces` n'ont pas de valeur. L'accepter en silence laisserait un
            # « 15 % sur une livraison offerte » affiche au comptoir, avec le chiffre dans la base.
            attrs["remise_valeur"] = None
        qr = attrs.get("qr", getattr(self.instance, "qr", None))
        user = self.context["request"].user
        from apps.accounts.exemption import exonere_de_facturation

        if qr is not None and qr.owner_id != user.pk and not exonere_de_facturation(user):
            raise serializers.ValidationError({"qr": "Ce QR ne vous appartient pas."})
        return attrs

    @extend_schema_field(serializers.DictField())
    def get_statut(self, objet) -> dict:
        """Le verdict complet de `apps.qr.promo.statut` — meme forme que la reponse de `/verifier/`."""
        from apps.qr import promo

        return promo.statut(objet).pour_api()

    def get_lien(self, objet) -> str:
        """L'URL a imprimer : le lien court du QR ; le code y est ajoute par la redirection, pas stocke."""
        if objet.qr_id is None:
            return ""
        from urllib.parse import urlencode, urlsplit, urlunsplit

        base = objet.qr.short_url
        morceaux = urlsplit(base)
        joint = (
            f"{morceaux.query}&{urlencode({'promo': objet.code})}"
            if morceaux.query
            else urlencode({"promo": objet.code})
        )
        return urlunsplit((morceaux.scheme, morceaux.netloc, morceaux.path, joint, morceaux.fragment))

    def get_expire_bientot(self, objet) -> bool:
        from django.utils import timezone

        return bool(objet.expire_le and 0 < (objet.expire_le - timezone.now()).total_seconds() < 7 * 86400)
