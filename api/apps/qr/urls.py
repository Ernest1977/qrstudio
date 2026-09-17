from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.qr.promo_views import PromoCodeViewSet
from apps.qr.views import QrViewSet

router = DefaultRouter()
router.register("qr", QrViewSet, basename="qr")
# Les codes promo sont un objet du meme domaine (ils vivent accroches a un QR) mais une porte separee :
# `/api/v1/promo/codes/` se documente, se throttle et se teste independamment du CRUD des QR.
router.register("promo/codes", PromoCodeViewSet, basename="promo-codes")

urlpatterns = [path("", include(router.urls))]
