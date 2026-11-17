import json
from pathlib import Path

from django.conf import settings
from django.contrib.gis.geos import GEOSException
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from apps.documents.models import Document
from apps.parcels import registry
from apps.parcels.models import Parcel
from apps.projects.models import Project

from .authentication import IsPartner, PartnerApiKeyAuthentication
from .serializers import ParcelSearchSerializer

SUBMITTED_STATUSES = (
    Project.Status.SUBMITTED,
    Project.Status.CHANGES_REQUESTED,
    Project.Status.ACCEPTED,
    Project.Status.REJECTED,
)


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


def _by_kind(documents: list[dict]) -> dict[str, list[dict]]:
    """The same document rows grouped by `kind` (ownership_proof, cadastral_extract, ...)."""
    grouped: dict[str, list[dict]] = {}
    for row in documents:
        grouped.setdefault(row["kind"], []).append(row)
    return grouped


def _operator_row(operator) -> dict:
    return {
        "id": operator.pk,
        "legal_name": operator.legal_name,
        "email": operator.email,
        "country_code": operator.country_code,
        "dcr_id": operator.dcr_id,
    }


def _include_documents(request) -> bool:
    return request.query_params.get("include_documents", "true").lower() not in ("0", "false")


def parcel_feature(
    parcel: Parcel,
    projects: list,
    request,
    include_documents: bool,
    project_documents: bool = True,
    registry_owners: tuple | None = None,
) -> dict:
    verification = getattr(parcel, "owner_verification", None)
    owner = getattr(parcel, "owner", None)
    links = {link.project_id: link for link in parcel.project_links.all()}
    documents = []
    if include_documents:
        documents += [
            _document_row(d, "parcel", request) for d in parcel.documents.filter(is_current=True)
        ]
        for project in projects if project_documents else []:
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
            "operator": _operator_row(parcel.operator),
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
            # the owner declared by the operator (DCR parcel_owner once the parcel is registered)
            "declared_owner": (
                {
                    "legal_name": owner.legal_name,
                    "owner_type": owner.owner_type,
                    "country_code": owner.country_code,
                    "document": owner.document_id,
                    "dcr_id": owner.dcr_id,
                }
                if owner
                else None
            ),
            # owners known to the land registry (France: legal persons only), to compare with
            # the declared owner; None when the reference is not a French cadastral id
            "registry_owners": (
                registry.registry_block(
                    parcel.cadastral_reference,
                    *registry_owners,
                    declared_name=owner.legal_name if owner else None,
                )
                if registry_owners
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
            "documents_by_kind": _by_kind(documents),
        },
    }


def _overlap(parcel_geometry, area) -> float:
    """
    Overlap of a parcel with the search geometry as a share of the smaller of the two, so a
    parcel inside the area and a small area inside a parcel both count fully, and a neighbour
    sharing only a border counts 0. Both are in EPSG:4326; the ratio is unit free.
    """
    try:
        smaller = min(parcel_geometry.area, area.area)
        if smaller <= 0:
            return 0.0
        return min(parcel_geometry.intersection(area).area / smaller, 1.0)
    except GEOSException:
        return 1.0  # an invalid stored geometry: keep it rather than hide it


def _registry_owners(parcels) -> tuple:
    """(owners by IDU, loaded vintage) for a set of parcels, in one query."""
    vintage = registry.loaded_vintage()
    if vintage is None:
        return {}, None
    return registry.owners_by_idu(p.cadastral_reference for p in parcels), vintage


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
            .select_related("operator", "owner_verification", "owner")
            .prefetch_related("project_links__project")
            .order_by("pk")
        )
        selected = []
        for parcel in parcels:
            projects = [link.project for link in parcel.project_links.all()]
            if not params["include_unsubmitted"]:
                projects = [p for p in projects if p.status in SUBMITTED_STATUSES]
                if not projects:
                    continue
            overlap = _overlap(parcel.geometry, params["geometry"])
            if params["min_overlap"] and overlap < params["min_overlap"]:
                continue  # a neighbour that only touches the geometry or overlaps it by a sliver
            selected.append((parcel, projects, overlap))
        owners = _registry_owners(parcel for parcel, _, _ in selected)
        features = []
        for parcel, projects, overlap in selected:
            feature = parcel_feature(
                parcel, projects, request, params["include_documents"], True, owners
            )
            feature["properties"]["overlap"] = round(overlap, 4)
            features.append(feature)
        return Response(
            {
                "type": "FeatureCollection",
                "features": features,
                "count": len(features),
                "include_unsubmitted": params["include_unsubmitted"],
            }
        )


class ActivityByRegistryIdView(PartnerView):
    """
    One activity by its DCR registry id (`activity_id`): the activity, its operator, the
    activity-level documents and its parcels as GeoJSON, each with its own documents
    (ownership proofs, cadastral extracts, ...). Only activities submitted to DCR.
    `?include_documents=false` leaves the documents out.
    """

    @extend_schema(responses={200: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    def get(self, request, dcr_id=None):
        project = (
            Project.objects.select_related("operator")
            .filter(dcr_id=dcr_id, status__in=SUBMITTED_STATUSES)
            .order_by("pk")
            .first()
        )
        if project is None:
            raise NotFound("No submitted activity with this registry id")
        include_documents = _include_documents(request)
        documents = []
        if include_documents:
            documents = [
                _document_row(d, "project", request)
                for d in project.documents.filter(is_current=True)
            ]
        links = (
            project.parcel_links.select_related(
                "parcel__operator", "parcel__owner_verification", "parcel__owner"
            )
            .prefetch_related("parcel__project_links")
            .order_by("parcel_id")
        )
        owners = _registry_owners(link.parcel for link in links)
        features = [
            parcel_feature(link.parcel, [project], request, include_documents, False, owners)
            for link in links
        ]
        return Response(
            {
                "id": project.pk,
                "dcr_id": project.dcr_id,
                "name": project.name,
                "type": project.type,
                "status": project.status,
                "start_date": project.start_date,
                "end_date": project.end_date,
                "submitted_at": project.submitted_at,
                "certification_scheme_id": project.certification_scheme_id,
                "operator": _operator_row(project.operator) if project.operator else None,
                "documents": documents,
                "documents_by_kind": _by_kind(documents),
                "parcels": {
                    "type": "FeatureCollection",
                    "count": len(features),
                    "features": features,
                },
            }
        )


class ParcelByRegistryIdView(PartnerView):
    """
    One parcel by its DCR registry id (`parcel_id`), in the same shape as a parcel search
    result: operator, owner verification, projects and documents. Only parcels of a
    submitted project unless `?include_unsubmitted=true`.
    """

    @extend_schema(responses={200: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    def get(self, request, dcr_id=None):
        parcel = (
            Parcel.objects.select_related("operator", "owner_verification", "owner")
            .prefetch_related("project_links__project")
            .filter(dcr_id=dcr_id)
            .order_by("pk")
            .first()
        )
        if parcel is None:
            raise NotFound("No parcel with this registry id")
        projects = [link.project for link in parcel.project_links.all()]
        if request.query_params.get("include_unsubmitted", "").lower() not in ("1", "true"):
            projects = [p for p in projects if p.status in SUBMITTED_STATUSES]
            if not projects:
                raise NotFound("No parcel with this registry id")
        owners = _registry_owners([parcel])
        return Response(
            parcel_feature(parcel, projects, request, _include_documents(request), True, owners)
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
                    "activity_by_registry_id": {
                        "method": "GET",
                        "url": base + "activities/{activity_id}/",
                    },
                    "parcel_by_registry_id": {
                        "method": "GET",
                        "url": base + "parcels/{parcel_id}/",
                    },
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
