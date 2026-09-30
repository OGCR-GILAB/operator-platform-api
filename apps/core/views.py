from django.conf import settings
from django.db import connection
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness/readiness probe used by Docker and the reverse proxy."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={200: OpenApiTypes.OBJECT, 503: OpenApiTypes.OBJECT})
    def get(self, request):
        database_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:  # noqa: BLE001 - any DB failure means "not ready"
            database_ok = False

        payload = {
            "status": "ok" if database_ok else "degraded",
            "database": "ok" if database_ok else "error",
            "version": settings.SPECTACULAR_SETTINGS["VERSION"],
        }
        code = status.HTTP_200_OK if database_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=code)
