from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from apps.core.serializers import MultiPolygonGeometryField
from apps.operators.models import Operator

from .models import Parcel, ParcelOwnerVerification, ProjectParcel

SYNC_FIELDS = ["dcr_id", "dcr_sync_status", "dcr_synced_at", "is_synced"]


class ParcelSerializer(serializers.ModelSerializer):
    operator = serializers.PrimaryKeyRelatedField(queryset=Operator.objects.all())
    operator_name = serializers.CharField(source="operator.legal_name", read_only=True)
    geometry = MultiPolygonGeometryField()
    area_ha = serializers.FloatField(read_only=True)
    is_synced = serializers.BooleanField(read_only=True)
    has_owner_verification = serializers.SerializerMethodField()
    project_count = serializers.IntegerField(source="project_links.count", read_only=True)
    document_count = serializers.SerializerMethodField()

    class Meta:
        model = Parcel
        exclude = ["dcr_response", "created_by"]
        read_only_fields = SYNC_FIELDS + ["created_at", "updated_at"]

    def get_document_count(self, obj) -> int:
        return obj.documents.filter(is_current=True).count()

    def get_has_owner_verification(self, obj) -> bool:
        return hasattr(obj, "owner_verification")

    def validate_operator(self, operator):
        request = self.context["request"]
        if request.user.is_staff or operator.memberships.filter(user=request.user).exists():
            return operator
        raise serializers.ValidationError("You are not a member of this operator")


class ParcelFeatureSerializer(GeoFeatureModelSerializer):
    """GeoJSON Feature representation for map layers."""

    area_ha = serializers.FloatField(read_only=True)

    class Meta:
        model = Parcel
        geo_field = "geometry"
        fields = ["id", "name", "cadastral_reference", "operator", "area_ha", "dcr_sync_status"]


class ParcelOwnerVerificationSerializer(serializers.ModelSerializer):
    is_synced = serializers.BooleanField(read_only=True)

    class Meta:
        model = ParcelOwnerVerification
        exclude = ["parcel", "dcr_response"]
        read_only_fields = SYNC_FIELDS + ["created_at", "updated_at"]


class ProjectParcelSerializer(serializers.ModelSerializer):
    """Link between a project and a parcel; `parcel` is written by id, read nested."""

    parcel = serializers.PrimaryKeyRelatedField(queryset=Parcel.objects.all())
    parcel_detail = ParcelSerializer(source="parcel", read_only=True)
    is_synced = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProjectParcel
        exclude = ["project", "dcr_response"]
        read_only_fields = SYNC_FIELDS + ["created_at", "updated_at"]

    def validate_parcel(self, parcel):
        request = self.context["request"]
        project = self.context["project"]
        if (
            not request.user.is_staff
            and not parcel.operator.memberships.filter(user=request.user).exists()
        ):
            raise serializers.ValidationError(
                "You are not a member of the operator owning this parcel"
            )
        if project.operator_id and parcel.operator_id != project.operator_id:
            raise serializers.ValidationError(
                "Parcel belongs to a different operator than the project"
            )
        if project.parcel_links.filter(parcel=parcel).exists():
            raise serializers.ValidationError("Parcel is already linked to this project")
        return parcel
