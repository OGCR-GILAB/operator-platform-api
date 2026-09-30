from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import Conflict
from apps.core.mixins import OneToOneSubresourceMixin

from .models import Parcel
from .serializers import (
    ParcelFeatureSerializer,
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
        queryset = Parcel.objects.select_related("operator", "owner_verification")
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
