from rest_framework import serializers

from apps.core import reference
from apps.core.serializers import MultiPolygonGeometryField
from apps.operators.models import Operator

from .models import ActivityPlan, MonitoringPlan, Project

SYNC_FIELDS = ["dcr_id", "dcr_sync_status", "dcr_synced_at", "is_synced"]


class ActivityPlanSerializer(serializers.ModelSerializer):
    is_synced = serializers.BooleanField(read_only=True)

    class Meta:
        model = ActivityPlan
        exclude = ["project", "dcr_response"]
        read_only_fields = SYNC_FIELDS + ["created_at", "updated_at"]


class MonitoringPlanSerializer(serializers.ModelSerializer):
    is_synced = serializers.BooleanField(read_only=True)

    class Meta:
        model = MonitoringPlan
        exclude = ["project", "dcr_response"]
        read_only_fields = SYNC_FIELDS + ["created_at", "updated_at"]

    def validate_monitored_data_parameters(self, value):
        for item in value:
            if not isinstance(item, dict) or not item.get("name"):
                raise serializers.ValidationError("Each parameter needs at least a name")
        return value


class ProjectSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(read_only=True)
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    operator = serializers.PrimaryKeyRelatedField(
        queryset=Operator.objects.all(), required=False, allow_null=True
    )
    operator_name = serializers.CharField(source="operator.legal_name", read_only=True)
    geometry = MultiPolygonGeometryField(required=False, allow_null=True)
    is_editable = serializers.BooleanField(read_only=True)
    is_synced = serializers.BooleanField(read_only=True)
    has_plan = serializers.SerializerMethodField()
    has_monitoring_plan = serializers.SerializerMethodField()
    parcel_count = serializers.IntegerField(source="parcel_links.count", read_only=True)
    document_count = serializers.SerializerMethodField()

    class Meta:
        model = Project
        exclude = ["dcr_response"]
        read_only_fields = SYNC_FIELDS + ["status", "submitted_at", "created_at", "updated_at"]

    def get_document_count(self, obj) -> int:
        return obj.documents.filter(is_current=True).count()

    def get_has_plan(self, obj) -> bool:
        return hasattr(obj, "plan")

    def get_has_monitoring_plan(self, obj) -> bool:
        return hasattr(obj, "monitoring_plan")

    def validate_operator(self, operator):
        request = self.context["request"]
        if operator is None or request.user.is_staff:
            return operator
        if not operator.memberships.filter(user=request.user).exists():
            raise serializers.ValidationError("You are not a member of this operator")
        return operator

    def validate_country_code(self, value):
        value = (value or "").strip().upper()
        if value and not reference.is_valid_country(value):
            raise serializers.ValidationError("Use an ISO 3166-1 alpha-2 code, e.g. DE")
        return value

    def validate(self, attrs):
        unit = attrs.get("unit_types")
        if unit:
            implied = reference.activity_type_for_unit(unit)
            if "type" in self.initial_data and attrs.get("type") != implied:
                raise serializers.ValidationError(
                    {"type": f"unit_types {unit!r} implies type {implied}"}
                )
            attrs["type"] = implied
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "must not be before start_date"})
        return attrs


class SubmissionErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    step = serializers.CharField(required=False)
    problems = serializers.ListField(child=serializers.CharField(), required=False)


class ReadinessCheckSerializer(serializers.Serializer):
    code = serializers.CharField()
    ok = serializers.BooleanField()
    level = serializers.ChoiceField(choices=["error", "warning"])
    message = serializers.CharField()


class ReadinessSerializer(serializers.Serializer):
    ready = serializers.BooleanField()
    editable = serializers.BooleanField()
    errors = serializers.ListField(child=serializers.CharField())
    warnings = serializers.ListField(child=serializers.CharField())
    checks = ReadinessCheckSerializer(many=True)


class DCRParcelStatusSerializer(serializers.Serializer):
    parcel = serializers.IntegerField()
    parcel_dcr_id = serializers.CharField(allow_blank=True)
    status_code = serializers.CharField()
    status_message = serializers.CharField(allow_blank=True)
    amount = serializers.IntegerField(allow_null=True)
    owner_verification = serializers.DictField(allow_null=True)


class DCRStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    dcr_id = serializers.CharField(allow_blank=True)
    verification = serializers.DictField(allow_null=True)
    parcels = DCRParcelStatusSerializer(many=True)
    checked_at = serializers.DateTimeField(allow_null=True)
