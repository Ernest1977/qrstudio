"""API des codes promo d'une campagne (palier Entreprise uniquement).

Le contrat, en trois phrases : le compte **génère** des codes (jamais il ne les invente — un code saisi est
un code qui se télescope avec celui du voisin), chaque code porte une **date de fin obligatoire** et un
plafond d'usages optionnel, et la **vérification se fait au scan**, dans la redirection, pas dans le
navigateur du client final.

Ce fichier ne décide donc pas de la validité : il appelle `apps.qr.promo.statut`, la même règle que la
redirection. Un endpoint qui aurait sa propre copie de la règle est un endpoint qui dit « valide » quand
le client au comptoir se prend « offre terminée ».
"""

from __future__ import annotations

import logging
from datetime import UTC

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.exceptions import ApiError
from apps.common.throttling import QrWriteThrottle
from apps.qr.models import PromoCode
from apps.qr.serializers import PromoCodeSerializer

logger = logging.getLogger(__name__)


class PromoCodeViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """`/api/v1/promo/codes/` — CRUD borne au compte, plus `generer`, `verifier` et `prolonger`."""

    serializer_class = PromoCodeSerializer
    throttle_classes = [QrWriteThrottle]
    filterset_fields = ("qr", "actif", "remise_type")
    # `lookup_field = "id"` comme `QrViewSet` : le routeur doit rendre le meme type d'URL partout.
    lookup_field = "id"
    # Pas d'`ordering` ici : la pagination par curseur (`apps.common.pagination.CursorById`) impose son
    # tri `-created_at, -id`. Le surcharger avec un champ qui n'existe pas casse `order_by` en FieldError
    # — vu en test, et le message etait trois ecrans plus bas que la cause.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        from apps.accounts.exemption import exonere_de_facturation

        base = PromoCode.objects.select_related("qr", "owner")
        if exonere_de_facturation(self.request.user):
            # Le personnel voit tous les codes : c'est lui qui répond au ticket « mon code ne marche pas »,
            # et il ne peut pas le faire depuis le compte d'un autre.
            return base
        return base.filter(owner=self.request.user)

    def _exiger_capacite(self, user, *, quantite: int = 1) -> None:
        from apps.accounts import plans
        from apps.qr.rendus import exiger_capacite

        exiger_capacite(user, "codes_promo", quoi="Les codes promo")
        plafond = plans.limite(user.plan_effectif, "codes_promo_max", 0)
        if plafond is None:
            return
        deja = PromoCode.objects.filter(owner=user, actif=True).count()
        if deja + quantite > plafond:
            raise ApiError(
                "quota_exceeded",
                f"Votre palier autorise {plafond} codes actifs, vous en avez deja {deja}.",
                status_code=402,
                details={"used": deja, "limit": plafond, "demande": quantite, "palier": user.plan_effectif},
            )

    def perform_create(self, serializer):
        from apps.qr import promo

        self._exiger_capacite(self.request.user)
        # Le code est toujours regenere ici : `code` est en lecture seule dans le serializer, et le seul
        # chemin qui ecrit un code fourni de l'exterieur serait une collision cherchee avec `generer`.
        code = promo.generer_code(libelle=serializer.validated_data.get("libelle") or "")
        serializer.save(owner=self.request.user, code=code)
        logger.info("code promo cree owner=%s id=%s", self.request.user.pk, serializer.instance.pk)

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        # Supprimer une offre en cours de campagne doit retirer la verification du chemin de
        # redirection : `PromoCode.delete()` s'en charge (il invalide le cache du QR lie).
        instance.delete()

    @action(detail=False, methods=["post"], url_path="generer")
    def generer(self, request):
        """Un lot de codes d'un coup : `{"quantite": 25, "expire_le": "…", "libelle": "…"}`.

        Le lot est la raison d'être du palier : distribuer 25 codes a une equipe de salle ne se fait pas
        en 25 formulaires. L'unicite par compte est garantie par la contrainte en base, et le tirage est
        borne (1 a 500) pour qu'un client ne puisse pas saturer la table avec un seul appel.
        """
        from datetime import datetime

        from django.utils.dateparse import parse_datetime

        from apps.qr import promo

        try:
            quantite = int(request.data.get("quantite") or 1)
        except (TypeError, ValueError):
            raise ApiError("quantite_invalide", "`quantite` doit être un entier.") from None
        if not 1 <= quantite <= 500:
            raise ApiError("quantite_invalide", "`quantite` doit être entre 1 et 500 par lot.")
        brut = request.data.get("expire_le")
        expire_le = brut if isinstance(brut, datetime) else parse_datetime(str(brut or ""))
        if expire_le is None:
            raise ApiError("expire_le_requis", "`expire_le` est obligatoire (date ISO 8601).", status_code=400)
        if expire_le.tzinfo is None:
            expire_le = expire_le.replace(tzinfo=UTC)
        self._exiger_capacite(request.user, quantite=quantite)

        # La forme passe par le serializer (mêmes règles que le POST unitaire), les codes sont ajoutés à
        # l'écriture : un lot qui creuserait sa propre validation créerait des lignes qu'un POST refuse.
        deja_vus = set(self.get_queryset().values_list("code", flat=True))
        codes = promo.generer_lot(quantite, libelle=str(request.data.get("libelle") or ""), deja_vus=deja_vus)
        commun = {
            "libelle": str(request.data.get("libelle") or "")[:160],
            "remise_type": str(request.data.get("remise_type") or "pourcentage"),
            "remise_valeur": request.data.get("remise_valeur"),
            "devise": str(request.data.get("devise") or "EUR")[:3],
            "expire_le": expire_le,
            "usages_max": request.data.get("usages_max"),
            "qr": request.data.get("qr") or None,
            "actif": True,
        }
        serialise = PromoCodeSerializer(data=[commun] * quantite, many=True, context={"request": request})
        serialise.is_valid(raise_exception=True)
        from django.db import transaction

        with transaction.atomic():
            for code in codes:
                # `create()` du serializer ne peut pas recevoir de kwargs supplementaires par element :
                # la ligne est assemblee ici, avec les champs deja valides au-dessus.
                PromoCode.objects.create(owner=request.user, code=code, **serialise.validated_data[codes.index(code)])
        lignes = list(PromoCode.objects.filter(owner=request.user, code__in=codes).select_related("qr"))
        corps = PromoCodeSerializer(lignes, many=True, context={"request": request})
        return Response(
            {
                "cree": len(codes),
                "expire_le": expire_le.isoformat(),
                "codes": corps.data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post", "get"], url_path="verifier")
    def verifier(self, request):
        """« Ce code est-il encore bon ? » — la caisse du commerçant, ou le test d'une campagne.

        Lecture seule : la consommation d'usage se fait au scan (`marquer_utilise` dans la redirection),
        pas ici. Un endpoint de verification qui consomme permettrait de bruler une offre par simple
        requete de controle.
        """
        from apps.qr import promo

        code = promo.cle_normalisee(request.data.get("code") or request.query_params.get("code") or "")
        if not code:
            raise ApiError("code_requis", "`code` est requis.", status_code=400)
        ligne = self.get_queryset().filter(code=code).first()
        if ligne is None:
            # Pas un 404 : pour la caisse, « inconnu » et « pas a moi » se repondent de la meme facon, et
            # l'existence d'un code chez un autre compte n'a rien a apprendre ici.
            return Response({"valide": False, "motif": "inconnu", "message": "Code inconnu pour ce compte."})
        return Response({**promo.statut(ligne).pour_api(), "code": ligne.code, "libelle": ligne.libelle})

    @action(detail=True, methods=["post"], url_path="prolonger")
    def prolonger(self, request, id=None):
        """Decaler la fin d'une offre, de `jours` jours (maximum 90) — et rien d'autre.

        Un `PATCH expire_le` suffit techniquement ; cet endpoint existe parce que la rallonge est
        l'operation commerciale reelle (« on prolonge l'operation d'une semaine ») et qu'elle doit laisser
        une trace lisible dans les journaux, pas noyer un champ parmi d'autres.
        """
        from datetime import timedelta

        from django.utils import timezone

        ligne = self.get_object()
        try:
            jours = int(request.data.get("jours", 7))
        except (TypeError, ValueError):
            raise ApiError("jours_invalide", "`jours` doit être un entier.") from None
        if not 1 <= jours <= 90:
            raise ApiError("jours_invalide", "`jours` doit être entre 1 et 90.")
        ligne.expire_le = max(ligne.expire_le, timezone.now()) + timedelta(days=jours)
        if not ligne.actif:
            ligne.actif = True  # rallonger une offre arrete la remet en ligne : c'est ce que le mot veut dire
        ligne.save(update_fields=["expire_le", "actif"])
        logger.info("code promo prolonge id=%s jours=%s owner=%s", ligne.pk, jours, request.user.pk)
        return Response(PromoCodeSerializer(ligne, context={"request": request}).data)
