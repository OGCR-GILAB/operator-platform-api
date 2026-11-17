from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from apps.core import reference
from apps.core.serializers import MultiPolygonGeometryField
from apps.documents.models import Document
from apps.operators.models import Operator

from . import registry
from .models import Parcel, ParcelOwner, ParcelOwnerVerification, ProjectParcel

SYNC_FIELDS = ["dcr_id", "dcr_sync_status", "dcr_synced_at", "is_synced"]


class ParcelSerializer(serializers.ModelSerializer):
    operator = serializers.PrimaryKeyRelatedField(queryset=Operator.objects.all())
    operator_name = serializers.CharField(source="operator.legal_name", read_only=True)
    geometry = MultiPolygonGeometryField()
    area_ha = serializers.FloatField(read_only=True)
    is_synced = serializers.BooleanField(read_only=True)
    has_owner_verification = serializers.SerializerMethodField()
    has_owner = serializers.SerializerMethodField()
    owner_is_complete = serializers.SerializerMethodField()
    registry_match = serializers.SerializerMethodField()
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

    def get_has_owner(self, obj) -> bool:
        return hasattr(obj, "owner")

    def get_owner_is_complete(self, obj) -> bool | None:
        """Whether the declared owner is complete; None while nothing is declared."""
        owner = getattr(obj, "owner", None)
        return owner.is_complete if owner else None

    def get_registry_match(self, obj) -> bool | None:
        """`matches_declared` of `/parcels/{id}/registry-owners/`, for list rows."""
        owner = getattr(obj, "owner", None)
        block = registry.registry_block(
            obj.cadastral_reference,
            *self._registry(),
            declared_name=owner.legal_name if owner else None,
        )
        return block["matches_declared"] if block else None

    def _registry(self) -> tuple:
        """
        Registry owners of every parcel the root serializer renders, read once per response
        (a page of parcels, or the parcel links of an activity) and kept in the context.
        """
        context = self.context
        if "_registry" not in context:
            items = self.root.instance
            if not isinstance(items, list | tuple | QuerySet):
                items = [items]
            parcels = [i if isinstance(i, Parcel) else getattr(i, "parcel", None) for i in items]
            vintage = registry.loaded_vintage()
            owners = (
                registry.owners_by_idu(p.cadastral_reference for p in parcels if p)
                if vintage
                else {}
            )
            context["_registry"] = (owners, vintage)
        return context["_registry"]

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


class ParcelOwnerSerializer(serializers.ModelSerializer):
    """The owner declared by the operator; `document` is a current document of this parcel."""

    document = serializers.PrimaryKeyRelatedField(
        queryset=Document.objects.all(), allow_null=True, required=False
    )
    document_title = serializers.CharField(source="document.title", read_only=True, default=None)
    is_synced = serializers.BooleanField(read_only=True)
    is_complete = serializers.BooleanField(read_only=True)

    class Meta:
        model = ParcelOwner
        exclude = ["parcel", "dcr_response"]
        read_only_fields = SYNC_FIELDS + [
            "supporting_document_dcr_id",
            "created_at",
            "updated_at",
        ]

    def validate_country_code(self, value: str) -> str:
        value = value.strip().upper()
        if not reference.is_valid_country(value):
            raise serializers.ValidationError("Use an ISO 3166-1 alpha-2 code, e.g. FR")
        return value

    def validate_document(self, document):
        parcel = self.context.get("parcel")
        if document is None:
            return document
        if parcel is not None and document.parcel_id != parcel.pk:
            raise serializers.ValidationError("Pick a document uploaded to this parcel")
        if not document.is_current:
            raise serializers.ValidationError("Pick the current version of the document")
        return document


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
