"""Routes de facturation.

Deux mondes, deux traitements : les routes **clients** (demander un lien, consulter son état) sont
authentifiées et plafonnées en débit ; la route **fournisseur** (le webhook) est publique par
nécessité et protégée par la signature au lieu de la session. C'est la seule route du projet où
`AllowAny` est légitime, et la seule où le corps n'est pas de confiance avant vérification.
"""

from __future__ import annotations

import logging

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common import throttling

logger = logging.getLogger(__name__)


class _FacturationMixin:
    """Authentifié, plafonné.

    `authentication_classes` est laisse herite — et c'est un piege ou je suis tombe : le mettre a `[]`
    pour « expliciter » supprime en realite SessionAuthentication, donc `request.user` devient
    anonyme et toutes les routes repondent 403 permission_denied a un client pourtant connecte. Un
    attribut herite n'est pas un oubli : c'est la configuration par defaut, Centralisee dans les
    reglages DRF, et le repeter ici la remplace.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [throttling.StatsThrottle]


class CheckoutView(_FacturationMixin, APIView):
    """`POST /api/v1/billing/checkout` — demande un lien de paiement, n'accorde rien.

    Le `palier` vient du corps de la requête, mais ce n'est pas une autorisation : il est recopié dans
    le `metadata` de la session, et seule la confirmation signée du fournisseur applique le plan.
    """

    @extend_schema(request=None, responses=None)
    def post(self, request):
        from apps.billing import services, stripe_api

        try:
            resultat = services.lien_checkout(request.user, palier_code=request.data.get("palier", ""))
        except stripe_api.ErreurStripe as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.statut)
        return Response(resultat, status=201)


class PortailView(_FacturationMixin, APIView):
    """Le portail client Stripe : moyen de paiement, factures, annulation. On ne réinvente pas ça."""

    @extend_schema(request=None, responses=None)
    def post(self, request):
        from apps.billing import services, stripe_api

        try:
            return Response(services.lien_portail(request.user))
        except stripe_api.ErreurStripe as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.statut)


class EtatView(_FacturationMixin, APIView):
    """État de la licence côté facturation — ce que le front affiche sur `/facturation`."""

    @extend_schema(request=None, responses=None)
    def get(self, request):
        from apps.accounts import plans
        from apps.billing.models import Abonnement

        abo = Abonnement.objects.filter(user=request.user).first()
        info = plans.tableau_pour_api(request.user.plan_effectif)
        if abo is None:
            return Response({"abonnement": None, "licence": info, "acorde": request.user.plan_effectif != "free"})
        return Response(
            {
                "abonnement": {
                    "fournisseur": abo.fournisseur,
                    "statut": abo.statut,
                    "palier": abo.palier,
                    "periode_fin": abo.periode_fin,
                    "en_sursis_jusqu_a": abo.en_sursis_jusqu_a,
                    "annule_le": abo.annule_le,
                    "en_registre": abo.en_registre,
                },
                "licence": info,
                "acorde": abo.en_registre,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class WebhookStripeView(APIView):
    """`POST /api/v1/billing/webhook/stripe` — la seule écriture de plan acceptée par le serveur.

    `csrf_exempt` est inévitable (Stripe ignore notre jeton) ; la protection est la signature
    HMAC sur le corps brut, lue **avant** tout parsing métier, et la tolérance d'horodatage qui rend le
    rejeu d'une capture inutile au-delà de cinq minutes.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes: list = []

    @extend_schema(request=None, responses=None)
    def post(self, request):
        from django.conf import settings

        from apps.billing import services, stripe_api

        valide, motif = stripe_api.verifier_signature(
            request.body, request.headers.get("Stripe-Signature"), settings.STRIPE_WEBHOOK_SECRET
        )
        if not valide:
            logger.warning("facturation webhook refuse motif=%s ip=%s", motif, request.client_ip)
            return Response(
                {"error": {"code": "signature_invalide", "message": f"Webhook rejeté ({motif})."}}, status=400
            )
        try:
            rapport = services.appliquer_webhook_stripe(request.body, secret_ok=True)
        except stripe_api.ErreurStripe as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.statut)
        return Response(rapport, status=200)


class MobileDemandeView(_FacturationMixin, APIView):
    """Démarre un paiement par agrégateur mobile (Satispay, Orange Money, MTN MoMo…)."""

    @extend_schema(request=None, responses=None)
    def post(self, request):
        from apps.billing import services, stripe_api

        try:
            resultat = services.demande_paiement_mobile(
                request.user,
                palier_code=request.data.get("palier", ""),
                telephone=str(request.data.get("telephone") or ""),
            )
        except stripe_api.ErreurStripe as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.statut)
        return Response(resultat, status=201)


class MobileEtatView(_FacturationMixin, APIView):
    """Sondage de la demande : le mobile est asynchrone, le front doit pouvoir attendre à voix haute."""

    @extend_schema(request=None, responses=None)
    def get(self, request, reference):
        from apps.billing import services, stripe_api

        try:
            paiement = services.etat_paiement_mobile(reference, request.user)
        except stripe_api.ErreurStripe as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.statut)
        return Response(
            {
                "reference": paiement.reference,
                "statut": paiement.statut,
                "palier": paiement.palier,
                "expire_le": paiement.expire_le,
                "confirme_le": paiement.confirme_le,
                "instruction": (paiement.detail or {}).get("instruction", ""),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class MobileCallbackView(APIView):
    """Rappel de l'agrégateur. Même discipline que Stripe : signature d'abord, métier ensuite."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes: list = []

    @extend_schema(request=None, responses=None)
    def post(self, request):
        # Chaque agreegateur pose sa signature dans un en-tete qui lui appartient (`Verif-Hash` chez
        # Flutterwave). C'est le fournisseur qui dit ou regarder, pas une liste figee cote vue : sinon
        # le branchement d'un nouvel agreegateur imposerait de modifier la vue.
        from apps.billing import mobile as mobile_mod
        from apps.billing import services, stripe_api

        noms = mobile_mod.fournisseur_actif().entetes_signature
        entete = next((request.headers.get(nom) for nom in noms if request.headers.get(nom)), None)
        try:
            rapport = services.traiter_rappel_mobile(request.body, entete)
        except stripe_api.ErreurStripe as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.statut)
        if rapport.get("refus"):
            # 400 cote transport (l'agreegateur peut renvoyer une correction), mais le refus est deja
            # ecote en base : la preuve survit a la reponse.
            return Response({"error": {"code": "montant_incoherent", "message": rapport["refus"]}}, status=400)
        return Response(rapport, status=200)
