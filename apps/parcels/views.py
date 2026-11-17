from types import SimpleNamespace

from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import Conflict
from apps.core.mixins import OneToOneSubresourceMixin

from . import registry
from .models import Parcel
from .serializers import (
    ParcelFeatureSerializer,
    ParcelOwnerSerializer,
    ParcelOwnerVerificationSerializer,
    ParcelSerializer,
)


class ParcelViewSet(OneToOneSubresourceMixin, viewsets.ModelViewSet):
    """
    Parcels of the operators the current user belongs to (staff sees all).

    Geometry is GeoJSON (Polygon or MultiPolygon, any SRID; stored as EPSG:4326).
    Parcels are pushed to DCR only when a project that uses them is submitted.
    """

    queryset = Parcel.objects.none()
    serializer_class = ParcelSerializer
    filterset_fields = ["operator", "dcr_sync_status", "cadastral_reference"]
    search_fields = ["name", "cadastral_reference"]
    ordering_fields = ["created_at", "updated_at", "name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Parcel.objects.none()
        queryset = Parcel.objects.select_related("operator", "owner_verification", "owner")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(operator__memberships__user=self.request.user).distinct()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        self._check_editable(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_editable(instance)
        if instance.project_links.exists():
            raise Conflict("Parcel is linked to a project; unlink it first")
        instance.delete()

    @staticmethod
    def _check_editable(parcel):
        if parcel.is_synced:
            raise Conflict("Parcel is already registered on DCR and cannot be changed locally")
        if parcel.project_links.exclude(project__status__in=("draft", "ready")).exists():
            raise Conflict("Parcel is part of a submitted project")

    @extend_schema(responses={200: ParcelFeatureSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def geojson(self, request):
        """All visible parcels as a GeoJSON FeatureCollection (no pagination), for map layers."""
        queryset = self.filter_queryset(self.get_queryset())
        return Response(ParcelFeatureSerializer(queryset, many=True).data)

    @extend_schema(
        request=ParcelOwnerVerificationSerializer,
        responses={200: ParcelOwnerVerificationSerializer},
    )
    @action(detail=True, methods=["get", "put", "patch"], url_path="owner-verification")
    def owner_verification(self, request, pk=None):
        parcel = self.get_object()
        editable = (
            not parcel.owner_verification.is_synced
            if hasattr(parcel, "owner_verification")
            else True
        )
        return self.one_to_one(
            request, ParcelOwnerVerificationSerializer, "owner_verification", "parcel", editable
        )

    @extend_schema(responses={200: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["get"], url_path="registry-owners")
    def registry_owners(self, request, pk=None):
        """
        What the land registry says about the parcel's owners, to compare with (or prefill)
        the declared owner. France only: the DGFiP file of parcels held by legal persons.
        `404` when the parcel has no French cadastral reference or no registry is loaded.
        """
        parcel = self.get_object()
        vintage = registry.loaded_vintage()
        owners = registry.owners_by_idu([parcel.cadastral_reference]) if vintage else {}
        owner = getattr(parcel, "owner", None)
        block = registry.registry_block(
            parcel.cadastral_reference,
            owners,
            vintage,
            declared_name=owner.legal_name if owner else None,
        )
        if block is None:
            return Response({"detail": "No registry data for this parcel"}, status=404)
        return Response(block)

    @extend_schema(request=ParcelOwnerSerializer, responses={200: ParcelOwnerSerializer})
    @action(detail=True, methods=["get", "put", "patch"], url_path="owner")
    def owner(self, request, pk=None):
        """
        The owner declared by the operator (DCR `parcel_owner`). Editable while the parcel
        still accepts documents; a change after it was sent is sent again as an update.
        """
        parcel = self.get_object()
        instance = getattr(parcel, "owner", None)
        if request.method == "GET":
            if instance is None:
                return Response(status=404)
            return Response(ParcelOwnerSerializer(instance).data)
        if not parcel.accepts_documents:
            raise Conflict("The parcel's verification is final; the owner can no longer change")
        partial = request.method == "PATCH" and instance is not None
        serializer = ParcelOwnerSerializer(
            instance, data=request.data, partial=partial, context={"parcel": parcel}
        )
        serializer.is_valid(raise_exception=True)
        owner = serializer.save(parcel=parcel)
        dcr_token = request.auth if isinstance(request.auth, str) else None
        if parcel.dcr_id and dcr_token:
            # the parcel is registered on DCR: send the declaration now rather than at the
            # next status sync
            from apps.projects import services

            services.sync_parcel_owners(
                services.get_client(), dcr_token, [SimpleNamespace(parcel=parcel)]
            )
            owner.refresh_from_db()
        data = ParcelOwnerSerializer(owner).data
        return Response(data, status=201 if instance is None else 200)
