from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dcr import reference as dcr_reference
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
    """
    Countries as DCR lists them (`country_id` = ISO 3166-1 alpha-2), sorted by name;
    the built-in ISO list while DCR is unreachable. Public (needed before login).
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        items = dcr_reference.vocabulary("countries")
        if not items:
            return Response(reference.countries())
        return Response([{"code": i["value"], "name": i["label"]} for i in items])


class VocabularyListView(APIView):
    """
    Controlled vocabularies published by DCR: countries, sustainable development goals,
    technologies/practices/processes and co-benefits, each as `{value, label}` items
    (`value` is the DCR id to store and submit). `sources` tells per list whether it came
    from DCR (`dcr`), the built-in fallback (`local`) or is currently `unavailable`. Public.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        body: dict = {}
        sources: dict = {}
        for name in dcr_reference.VOCABULARIES:
            items = dcr_reference.vocabulary(name)
            sources[name] = "dcr" if items is not None else "unavailable"
            if items is None and name == "countries":
                items = [{"value": c["code"], "label": c["name"]} for c in reference.countries()]
                sources[name] = "local"
            body[name] = items or []
        body["sources"] = sources
        return Response(body)
