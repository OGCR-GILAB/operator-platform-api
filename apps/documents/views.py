from pathlib import PurePosixPath

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.exceptions import Conflict

from .models import Document
from .serializers import DocumentSerializer, DocumentVersionSerializer, sha256_of

FROZEN = "Document is frozen (submitted project, registered parcel or forwarded)"


class DocumentViewSet(viewsets.ModelViewSet):
    """
    Supporting documents of projects and parcels.

    Upload with multipart/form-data: `file`, `kind`, `title`, and exactly one of
    `project` / `parcel`. Only current versions are listed unless `?all_versions=1`.
    Files are private: fetch them through `/download/`, never from a static URL.
    """

    queryset = Document.objects.none()
    serializer_class = DocumentSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filterset_fields = ["project", "parcel", "kind", "forward_status", "operator"]
    search_fields = ["title", "description", "original_name"]
    ordering_fields = ["uploaded_at", "title", "kind", "size"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Document.objects.none()
        user = self.request.user
        queryset = Document.objects.select_related(
            "project", "project__operator", "parcel", "parcel__operator", "uploaded_by"
        )
        if not user.is_staff:
            queryset = queryset.filter(
                Q(project__owner=user)
                | Q(project__operator__memberships__user=user)
                | Q(parcel__operator__memberships__user=user)
            ).distinct()
        all_versions = self.request.query_params.get("all_versions") in ("1", "true")
        if self.action == "list" and not all_versions:
            queryset = queryset.filter(is_current=True)
        return queryset

    def perform_update(self, serializer):
        if serializer.instance.is_frozen:
            raise Conflict(FROZEN)
        serializer.save()

    def perform_destroy(self, instance):
        if instance.is_frozen:
            raise Conflict(FROZEN)
        if hasattr(instance, "superseded_by"):
            raise Conflict("Older versions cannot be deleted; delete the current version instead")
        with transaction.atomic():
            previous = instance.supersedes
            storage, name = instance.file.storage, instance.file.name
            instance.delete()
            if previous is not None:
                previous.is_current = True
                previous.save(update_fields=["is_current"])
        storage.delete(name)

    @extend_schema(
        parameters=[
            OpenApiParameter("all_versions", bool, description="Include superseded versions")
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        document = self.get_object()
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.original_name,
            content_type=document.content_type or "application/octet-stream",
        )
        response["X-Checksum-SHA256"] = document.sha256
        return response

    @extend_schema(request=DocumentVersionSerializer, responses={201: DocumentSerializer})
    @action(detail=True, methods=["post"])
    def versions(self, request, pk=None):
        """Upload a new version; the current document is superseded but kept."""
        current = self.get_object()
        if current.is_frozen:
            raise Conflict(FROZEN)
        if not current.is_current:
            raise Conflict("Upload the new version against the current document")
        serializer = DocumentVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded = serializer.validated_data["file"]
        with transaction.atomic():
            new = Document.objects.create(
                project=current.project,
                parcel=current.parcel,
                operator=current.operator,
                uploaded_by=request.user,
                kind=current.kind,
                title=serializer.validated_data.get("title") or current.title,
                description=serializer.validated_data.get("description", current.description),
                file=uploaded,
                original_name=PurePosixPath(uploaded.name).name[:255],
                content_type=getattr(uploaded, "content_type", "") or "",
                size=uploaded.size,
                sha256=sha256_of(uploaded),
                version=current.version + 1,
                supersedes=current,
            )
            current.is_current = False
            current.save(update_fields=["is_current"])
        data = DocumentSerializer(new, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)
