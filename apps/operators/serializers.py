from rest_framework import serializers

from apps.accounts.models import User
from apps.core import reference

from .models import Operator, OperatorMembership


class OperatorSerializer(serializers.ModelSerializer):
    relationship = serializers.CharField(
        write_only=True,
        required=False,
        default="Managing Director",
        help_text="Relationship of the creating user to the operator",
    )
    my_relationship = serializers.SerializerMethodField()
    is_synced = serializers.BooleanField(read_only=True)

    class Meta:
        model = Operator
        fields = [
            "id",
            "legal_name",
            "email",
            "phone",
            "address_line_1",
            "address_line_2",
            "postcode",
            "country_code",
            "ogcr_wallet_address",
            "relationship",
            "my_relationship",
            "dcr_id",
            "dcr_sync_status",
            "dcr_synced_at",
            "is_synced",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "dcr_id",
            "dcr_sync_status",
            "dcr_synced_at",
            "created_at",
            "updated_at",
        ]

    def get_my_relationship(self, obj) -> str | None:
        user = self.context["request"].user
        membership = next((m for m in obj.memberships.all() if m.user_id == user.id), None)
        return membership.relationship if membership else None

    def validate_country_code(self, value: str) -> str:
        value = value.strip().upper()
        if not reference.is_valid_country(value):
            raise serializers.ValidationError("Use an ISO 3166-1 alpha-2 code, e.g. DE")
        return value

    def create(self, validated_data):
        relationship = validated_data.pop("relationship", "Managing Director")
        user = self.context["request"].user
        operator = Operator.objects.create(created_by=user, **validated_data)
        OperatorMembership.objects.create(operator=operator, user=user, relationship=relationship)
        return operator

    def update(self, instance, validated_data):
        validated_data.pop("relationship", None)
        return super().update(instance, validated_data)


class OperatorMembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = OperatorMembership
        fields = ["id", "user", "username", "relationship", "dcr_id", "dcr_sync_status"]
        read_only_fields = ["dcr_id", "dcr_sync_status"]


class AddMemberSerializer(serializers.Serializer):
    """Add an existing platform user (identified by username or e-mail) to an operator."""

    username = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    relationship = serializers.CharField(max_length=100, default="Employee")

    def validate(self, attrs):
        username, email = attrs.get("username"), attrs.get("email")
        if not username and not email:
            raise serializers.ValidationError("Provide username or email")
        user = (
            User.objects.filter(username=username).first()
            if username
            else User.objects.filter(email__iexact=email).first()
        )
        if user is None:
            # same message whether the account is unknown or simply never logged in here,
            # so the endpoint cannot be used to enumerate registered e-mail addresses
            raise serializers.ValidationError(
                "No platform user with that identifier. The person must register on DCR and "
                "log in here at least once."
            )
        attrs["user"] = user
        return attrs


class OperatorDCRSerializer(serializers.Serializer):
    dcr_id = serializers.CharField()
    dcr_synced_at = serializers.DateTimeField(allow_null=True)
    fetched_at = serializers.DateTimeField()
    record = serializers.DictField()
    ogcr_wallet_address = serializers.CharField(allow_blank=True)
