from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.documents.models import Document
from apps.projects.models import Project

from . import reference


class ReferenceView(APIView):
    """Vocabularies the frontend should offer instead of free text. Public."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(
            {
                "activity_types": [
                    {"value": v, "label": label} for v, label in reference.ACTIVITY_TYPES
                ],
                "unit_types": [
                    {"value": unit, "activity_type": activity}
                    for unit, activity in reference.UNIT_TYPES.items()
                ],
                "project_statuses": [
                    {"value": v, "label": label} for v, label in Project.Status.choices
                ],
                "verification_statuses": reference.VERIFICATION_STATUSES,
                "document_kinds": [
                    {"value": v, "label": label} for v, label in Document.Kind.choices
                ],
                "practice_examples": reference.PRACTICE_EXAMPLES,
                "cobenefit_examples": reference.COBENEFIT_EXAMPLES,
                "document_max_size_mb": settings.DOCUMENTS["MAX_SIZE_MB"],
                "document_allowed_extensions": sorted(settings.DOCUMENTS["ALLOWED_EXTENSIONS"]),
            }
        )


class CountryListView(APIView):
    """ISO 3166-1 alpha-2 countries, sorted by name. Public (needed before login)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(reference.countries())
