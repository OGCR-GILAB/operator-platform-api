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


MEDIA_KEYS = {"link", "title", "type", "author", "published_date", "image"}


def validate_media_links(value):
    """A list of URLs or of objects with `link` (URL) and optional descriptive fields."""
    if not isinstance(value, list):
        raise serializers.ValidationError("media_links must be a list")
    url = serializers.URLField()
    date = serializers.DateField()
    cleaned = []
    for i, item in enumerate(value):
        try:
            if isinstance(item, str):
                cleaned.append(url.run_validation(item))
                continue
            if not isinstance(item, dict) or "link" not in item:
                raise serializers.ValidationError("use a URL or an object with `link`")
            unknown = set(item) - MEDIA_KEYS
            if unknown:
                raise serializers.ValidationError(f"unknown keys: {', '.join(sorted(unknown))}")
            entry = {k: str(v).strip() for k, v in item.items() if v not in (None, "")}
            entry["link"] = url.run_validation(entry.get("link", ""))
            if entry.get("image"):
                entry["image"] = url.run_validation(entry["image"])
            if entry.get("published_date"):
                entry["published_date"] = date.run_validation(entry["published_date"]).isoformat()
            cleaned.append(entry)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({i: exc.detail}) from exc
    return cleaned


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
        exclude = ["dcr_response", "dcr_links"]
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

    def validate_media_links(self, value):
        return validate_media_links(value)

    def _id_list(self, value, name):
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise serializers.ValidationError(f"{name} must be a list of non-empty strings")
        return list(dict.fromkeys(v.strip() for v in value))

    def validate_technologies_practices_processes(self, value):
        return self._id_list(value, "technologies_practices_processes")

    def validate_cobenefits(self, value):
        return self._id_list(value, "cobenefits")

    def validate_sustainable_development_goals(self, value):
        return self._id_list(value, "sustainable_development_goals")

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
    bank_id = serializers.CharField(required=False)
    missing_roles = serializers.ListField(child=serializers.CharField(), required=False)


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
    name = serializers.CharField(allow_blank=True)
    cadastral_reference = serializers.CharField(allow_blank=True)
    has_verdict = serializers.BooleanField(
        help_text="True when the certifier recorded a verdict for this parcel itself; "
        "otherwise the activity verification applies"
    )
    status_code = serializers.CharField()
    status_message = serializers.CharField(allow_blank=True)
    amount = serializers.IntegerField(allow_null=True)
    owner_verification = serializers.DictField(allow_null=True)


class AmendmentSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=["validation", "verification"], allow_null=True)
    status_code = serializers.CharField()
    message = serializers.CharField(allow_blank=True)


class DCRStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    amendment = AmendmentSerializer(
        allow_null=True,
        help_text="Set while the certifier waits for changes (status changes_requested)",
    )
    dcr_id = serializers.CharField(allow_blank=True)
    verification = serializers.DictField(allow_null=True)
    parcels = DCRParcelStatusSerializer(many=True)
    certificate = serializers.DictField(
        allow_null=True,
        help_text="Newest certificate_of_compliance with certification_status certified/active",
    )
    certificates = serializers.ListField(
        child=serializers.DictField(), help_text="All certificates of compliance, newest first"
    )
    audit_reports = serializers.ListField(
        child=serializers.DictField(), help_text="Audit reports, newest first"
    )
    checked_at = serializers.DateTimeField(allow_null=True)
