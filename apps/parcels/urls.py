from rest_framework.routers import DefaultRouter

from .views import ParcelViewSet

router = DefaultRouter()
router.register("parcels", ParcelViewSet, basename="parcel")

urlpatterns = router.urls
