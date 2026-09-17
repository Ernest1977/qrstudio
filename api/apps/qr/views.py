"""API des QR de l'utilisateur.

Propriété stricte : **404 et non 403** sur un QR qui appartient à quelqu'un d'autre. Un 403 confirme
que l'identifiant existe et ouvre une sonde d'énumération ; ici l'objet est tout simplement « absent »
de la queryset de l'utilisateur.
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common import throttling
from apps.common.throttling import QrWriteThrottle, StatsThrottle
from apps.qr import services
from apps.qr.filters import QrFilter
from apps.qr.models import QrCode
from apps.qr.rendus import RendusMixin
from apps.qr.serializers import QrSerializer

logger = logging.getLogger(__name__)


class QrViewSet(
    RendusMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """`/api/v1/qr/` — lecture, création, mise à jour (PATCH seulement), suppression logique."""

    serializer_class = QrSerializer
    filterset_class = QrFilter
    search_fields = ["label", "slug", "notes"]
    lookup_field = "id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_throttles(self):
        if self.action in {"create", "partial_update", "destroy", "pause", "resume", "duplicate", "restore"}:
            return [QrWriteThrottle()]
        if self.action == "stats":
            return [StatsThrottle()]
        return []

    def get_queryset(self):
        user = self.request.user
        # Le test d'identite passe **avant** le filtre : `filter(owner=<instance non enregistree>)`
        # leve un `ValueError` au moment de la construction, pas a l'execution. Cas reel : un compte
        # efface (RGPD) pendant que sa session ou son jeton vit encore — le client doit recevoir une
        # liste vide puis un 401 a l'ecriture, pas une 500.
        if not getattr(user, "is_authenticated", False) or user.pk is None:
            return QrCode.objects.none()
        qs = QrCode.objects.filter(owner=user)
        if self.action == "list":
            # `deleted_at` et `archived_at` sont exclus par défaut : un tableau de bord qui montre des
            # lignes mortes est un tableau de bord dont on se méfie. `?archived=true` / `?deleted=true`
            # les ramènent (les filtres les plus bas dans la pile les réautorisent explicitement).
            qs = qs.filter(deleted_at__isnull=True)
            if "archived" not in self.request.query_params:
                qs = qs.filter(archived_at__isnull=True)
        return qs.select_related("owner").order_by("-created_at", "-id")

    def perform_create(self, serializer):
        services.create_qr(owner=self.request.user, serializer=serializer)

    def perform_update(self, serializer):
        services.update_qr(qr=serializer.instance, actor=self.request.user, serializer=serializer)

    def perform_destroy(self, instance):
        services.soft_delete(instance, actor=self.request.user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        hard = request.query_params.get("hard") in {"1", "true", "yes"}
        if hard:
            services.hard_delete(instance, actor=request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def pause(self, request, id=None):
        qr = self.get_object()
        qr.pause(actor=request.user)
        return Response(QrSerializer(qr, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, id=None):
        qr = self.get_object()
        qr.resume(actor=request.user)
        return Response(QrSerializer(qr, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, id=None):
        qr = get_object_or_404(QrCode, pk=id, owner=request.user)
        qr.restore(actor=request.user)
        return Response(QrSerializer(qr, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def duplicate(self, request, id=None):
        source = self.get_object()
        clone = services.duplicate(source, actor=request.user)
        return Response(QrSerializer(clone, context=self.get_serializer_context()).data, status=201)

    @action(detail=True, methods=["get"])
    def history(self, request, id=None):
        qr = self.get_object()
        from apps.qr.serializers import QrVersionSerializer

        return Response(QrVersionSerializer(qr.versions.all()[:100], many=True).data)

    @action(detail=True, methods=["get"], throttle_classes=[throttling.QrImageThrottle])
    def image(self, request, id=None):
        """Rendu serveur (`png` par défaut, `svg`/`pdf` sur demande) — l'URL historique du front.

        Le corps est une delegation a `rendus.classique` : `png`/`svg`/`pdf` ne doivent pas exister deux
        fois avec deux politiques de cache. `/rendu/` accepte en plus `fmt=art|gif` (avec preuve de
        lisibilite dans les en-tetes) ; `/image/` reste l'URL courte, documentee et gravee dans le front.
        """
        from apps.qr import rendus

        fmt = (request.query_params.get("fmt") or "png").lower()
        return rendus.classique(request, self.get_object(), fmt)

    @action(detail=True, methods=["get"])
    def stats(self, request, id=None):
        """Totals + répartition par pays, lus sur l'agrégat (jamais sur les lignes de scan brutes)."""
        qr = self.get_object()
        from datetime import timedelta

        from django.utils import timezone

        from apps.analytics.aggregates import country_breakdown, daily_series
        from apps.common.exceptions import ApiError

        if not request.user.a_droit_a("analytics"):
            # Les statistiques sont le poste qui coute cher (l'ingest, les partitions, l'agregation).
            # Un 402 explicite vaut mieux qu'une liste de pays vide, qui ressemblerait a un bug de
            # mesure alors que c'est une porte fermee.
            from apps.accounts import plans

            requis = next(c for c in plans.ORDRE if "analytics" in plans.PALIERS[c].apporte)
            raise ApiError(
                "plan_required",
                f"Les statistiques de scan sont reservees au palier {plans.palier(requis).nom} "
                f"({plans.palier(requis).prix_eur} EUR/mois).",
                status_code=402,
                details={
                    "caracteristique": "analytics",
                    "palier_requis": requis,
                    "plan_actuel": request.user.plan_effectif,
                },
            )
        # La profondeur demandee est bornee par le palier, pas seulement par 365 : sans ca, un
        # client premium pourrait demander 10 ans d'historique a chaque ouverture de page.
        days = min(int(request.query_params.get("days", 30) or 30), max(1, request.user.jours_historique), 365)
        since = timezone.now().date() - timedelta(days=days)
        serie = daily_series(qr_id=qr.pk, since=since)
        from django.db.models import Sum

        from apps.analytics.models import QrDailyStats

        total_agregats = QrDailyStats.objects.filter(qr_id=qr.pk).aggregate(t=Sum("scans"))["t"] or 0
        return Response(
            {
                "qr_id": qr.pk,
                "slug": qr.slug,
                # `total` vient des **agregats**, pas du compteur denormalise : la regle du projet est
                # que `QrDailyStats` est la seule source de lecture des statistiques. Le compteur
                # temps reel (`scan_count_total`, alimente par Redis puis `flush_scan_counters`) est
                # expose separement — sans Redis, ou entre deux passages de beat, il est en retard, et
                # un tableau de bord qui affichait « 0 scans » a cote d'un graphe a 120 etait le
                # premier ecart visible de ce projet.
                "total": total_agregats,
                "total_fenetre": sum(ligne["scans"] for ligne in serie),
                "compteur_temps_reel": qr.scan_count_total,
                "window_days": days,
                "series": serie,
                "countries": country_breakdown(qr_id=qr.pk, since=since),
                "tracking_allowed": qr.owner.can_track,
            }
        )
