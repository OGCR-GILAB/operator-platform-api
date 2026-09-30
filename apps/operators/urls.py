from rest_framework.routers import DefaultRouter

from .views import OperatorViewSet

router = DefaultRouter()
router.register("operators", OperatorViewSet, basename="operator")

urlpatterns = router.urls
