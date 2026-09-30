from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "dcr_user_id",
            "dcr_provider",
            "is_staff",
        ]
        read_only_fields = fields


class DCRCapabilitySerializer(serializers.Serializer):
    allowed = serializers.BooleanField()
    missing_roles = serializers.ListField(child=serializers.CharField())


class DCRAccountSerializer(serializers.Serializer):
    """The user as DCR knows them (from the last /users/current call)."""

    user_id = serializers.CharField(allow_blank=True)
    username = serializers.CharField(allow_blank=True)
    email = serializers.CharField(allow_blank=True)
    provider = serializers.CharField(allow_blank=True)
    provider_id = serializers.CharField(allow_blank=True)
    roles = serializers.ListField(child=serializers.CharField())
    bank_scoped_roles = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text="Roles granted per bank; these do not apply to the system-level OGCR entities",
    )
    capabilities = serializers.DictField(child=DCRCapabilitySerializer())
    synced_at = serializers.DateTimeField(allow_null=True)


class MeSerializer(UserSerializer):
    dcr = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ["dcr"]
        read_only_fields = fields

    def get_dcr(self, obj) -> dict | None:
        from apps.dcr.roles import bank_scoped_roles, capabilities, role_names

        profile = obj.dcr_profile or {}
        if not profile and not obj.dcr_user_id:
            return None
        return {
            "user_id": obj.dcr_user_id or "",
            "username": profile.get("username", ""),
            "email": profile.get("email", ""),
            "provider": profile.get("provider", obj.dcr_provider),
            "provider_id": profile.get("provider_id", ""),
            "roles": role_names(profile),
            "bank_scoped_roles": bank_scoped_roles(profile),
            "capabilities": capabilities(profile),
            "synced_at": obj.dcr_synced_at,
        }
