import json
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from apps.documents.models import Document
from apps.parcels.models import Parcel
from apps.projects.models import Project

from .authentication import IsPartner, PartnerApiKeyAuthentication
from .serializers import ParcelSearchSerializer

SUBMITTED_STATUSES = (Project.Status.SUBMITTED, Project.Status.ACCEPTED, Project.Status.REJECTED)


class PartnerView(APIView):
    authentication_classes = [PartnerApiKeyAuthentication]
    permission_classes = [IsPartner]


def _document_row(document: Document, scope: str, request) -> dict:
    return {
        "id": document.pk,
        "scope": scope,
        "project": document.project_id,
        "kind": document.kind,
        "title": document.title,
        "description": document.description,
        "version": document.version,
        "original_name": document.original_name,
        "content_type": document.content_type,
        "size": document.size,
        "sha256": document.sha256,
        "uploaded_at": document.uploaded_at,
        "download_url": reverse("partner-document-download", args=[document.pk], request=request),
    }


def parcel_feature(parcel: Parcel, projects: list, request, include_documents: bool) -> dict:
    verification = getattr(parcel, "owner_verification", None)
    links = {link.project_id: link for link in parcel.project_links.all()}
    documents = []
    if include_documents:
        documents += [
            _document_row(d, "parcel", request) for d in parcel.documents.filter(is_current=True)
        ]
        for project in projects:
            documents += [
                _document_row(d, "project", request)
                for d in project.documents.filter(is_current=True)
            ]
    return {
        "type": "Feature",
        "id": parcel.pk,
        "geometry": json.loads(parcel.geometry.json),
        "properties": {
            "id": parcel.pk,
            "name": parcel.name,
            "cadastral_reference": parcel.cadastral_reference,
            "iacs_codes": parcel.iacs_codes,
            "lpis_codes": parcel.lpis_codes,
            "area_ha": parcel.area_ha,
            "dcr_id": parcel.dcr_id,
            "operator": {
                "id": parcel.operator_id,
                "legal_name": parcel.operator.legal_name,
                "email": parcel.operator.email,
                "country_code": parcel.operator.country_code,
                "dcr_id": parcel.operator.dcr_id,
            },
            "owner_verification": (
                {
                    "status_code": verification.status_code,
                    "status_message": verification.status_message,
                    "parcel_owner_legal_name": verification.parcel_owner_legal_name,
                    "authority": verification.authority,
                    "dcr_id": verification.dcr_id,
                }
                if verification
                else None
            ),
            "projects": [
                {
                    "id": project.pk,
                    "name": project.name,
                    "type": project.type,
                    "status": project.status,
                    "dcr_id": project.dcr_id,
                    "start_date": project.start_date,
                    "end_date": project.end_date,
                    "submitted_at": project.submitted_at,
                    "link_status": links[project.pk].status_code,
                    "amount": links[project.pk].amount,
                }
                for project in projects
            ],
            "documents": documents,
        },
    }


class ParcelSearchView(PartnerView):
    """
    Parcels intersecting a geometry, with operator, projects, owner verification and
    supporting documents. By default only parcels of at least one submitted project are
    returned; `include_unsubmitted` adds the rest.
    """

    @extend_schema(request=ParcelSearchSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = ParcelSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        params = serializer.validated_data
        parcels = (
            Parcel.objects.filter(geometry__intersects=params["geometry"])
            .select_related("operator", "owner_verification")
            .prefetch_related("project_links__project")
            .order_by("pk")
        )
        features = []
        for parcel in parcels:
            projects = [link.project for link in parcel.project_links.all()]
            if not params["include_unsubmitted"]:
                projects = [p for p in projects if p.status in SUBMITTED_STATUSES]
                if not projects:
                    continue
            features.append(parcel_feature(parcel, projects, request, params["include_documents"]))
        return Response(
            {
                "type": "FeatureCollection",
                "features": features,
                "count": len(features),
                "include_unsubmitted": params["include_unsubmitted"],
            }
        )


class PartnerDocumentDownloadView(PartnerView):
    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    def get(self, request, pk=None):
        document = get_object_or_404(Document, pk=pk)
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.original_name,
            content_type=document.content_type or "application/octet-stream",
        )
        response["X-Checksum-SHA256"] = document.sha256
        return response


class PartnerPingView(PartnerView):
    """Lets a partner verify its key."""

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response({"partner": request.auth.name, "status": "ok"})


class PartnerIndexView(APIView):
    """Public index of the partner API: what exists and where the documentation is."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        base = request.build_absolute_uri("/api/partner/")
        return Response(
            {
                "name": "OGCR Operator Platform - Partner API",
                "authentication": "Authorization: Api-Key <key issued by the platform team>",
                "documentation": request.build_absolute_uri("/api/partner/docs/"),
                "interactive_docs": request.build_absolute_uri("/api/docs/"),
                "endpoints": {
                    "ping": {"method": "GET", "url": base + "ping/"},
                    "parcel_search": {"method": "POST", "url": base + "parcels/search/"},
                    "document_download": {
                        "method": "GET",
                        "url": base + "documents/{id}/download/",
                    },
                },
            }
        )


class PartnerDocsView(APIView):
    """The partner guide (Markdown) served as plain text."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={(200, "text/markdown"): str})
    def get(self, request):
        text = (Path(settings.BASE_DIR) / "docs" / "partner-api.md").read_text(encoding="utf-8")
        return HttpResponse(text, content_type="text/markdown; charset=utf-8")
