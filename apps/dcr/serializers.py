import re

from rest_framework import serializers

from apps.accounts.serializers import UserSerializer


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)


class LoginResponseSerializer(serializers.Serializer):
    token = serializers.CharField()
    user = UserSerializer()


class CertificationSchemeSerializer(serializers.Serializer):
    certification_scheme_id = serializers.CharField()
    name = serializers.CharField()
    commission_decision_reference = serializers.CharField(required=False, allow_blank=True)
    certification_methodology = serializers.CharField(required=False, allow_blank=True)
    scheme_version_number = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    source = serializers.ChoiceField(choices=["dcr", "sample"], required=False)


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    username = serializers.CharField(
        required=False, allow_blank=True, help_text="Defaults to the e-mail address"
    )
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    mobile_phone_number = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=32,
        help_text="Optional, international format, e.g. +33 6 12 34 56 78",
    )

    def validate_password(self, value):
        from .reference import password_policy, password_satisfies

        # checked here so the user gets DCR's own wording before the DCR call;
        # skipped when DCR cannot report its policy (DCR still enforces it)
        policy = password_policy()
        if policy and not password_satisfies(value, policy):
            raise serializers.ValidationError(policy.get("description") or "Password too weak")
        return value

    def validate_mobile_phone_number(self, value):
        return validate_phone(value) if value else ""

    def validate(self, attrs):
        attrs["username"] = (attrs.get("username") or attrs["email"]).strip()
        return attrs


class RegisterResponseSerializer(serializers.Serializer):
    user = UserSerializer()
    detail = serializers.CharField()


PHONE_RE = re.compile(r"^\+[0-9][0-9 ()-]{5,30}$")


def validate_phone(value: str) -> str:
    value = value.strip()
    if not PHONE_RE.match(value) or sum(ch.isdigit() for ch in value) < 6:
        raise serializers.ValidationError(
            "Use the international format starting with +, e.g. +33 6 12 34 56 78"
        )
    return value


class EmailValidationSerializer(serializers.Serializer):
    token = serializers.CharField()


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()
    username = serializers.CharField(required=False, allow_blank=True)


class ResendValidationEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    username = serializers.CharField(
        required=False, allow_blank=True, help_text="Defaults to the e-mail address"
    )


class MobilePhoneSerializer(serializers.Serializer):
    mobile_phone_number = serializers.CharField(max_length=32)

    def validate_mobile_phone_number(self, value):
        return validate_phone(value)


class PasswordPolicyRuleSerializer(serializers.Serializer):
    description = serializers.CharField()
    min_length = serializers.IntegerField()
    max_length = serializers.IntegerField()
    required_character_classes = serializers.ListField(child=serializers.DictField())
    regex = serializers.CharField(required=False)


class PasswordPolicySerializer(serializers.Serializer):
    description = serializers.CharField()
    policies = PasswordPolicyRuleSerializer(many=True)


class DCRRoleStatusSerializer(serializers.Serializer):
    role = serializers.CharField()
    held = serializers.BooleanField(allow_null=True)
    held_by = serializers.DictField(child=serializers.BooleanField())
    error = serializers.CharField(allow_blank=True)


class DCRLiveCapabilitySerializer(serializers.Serializer):
    allowed = serializers.BooleanField()
    missing_roles = serializers.ListField(child=serializers.CharField())
    unknown_roles = serializers.ListField(child=serializers.CharField(), required=False)


class DCRPermissionsSerializer(serializers.Serializer):
    source = serializers.ChoiceField(
        choices=["live", "profile"],
        help_text="live = checked role by role on DCR; profile = from the login-time role list",
    )
    bank_id = serializers.CharField()
    checked_at = serializers.CharField(allow_null=True)
    roles = DCRRoleStatusSerializer(many=True)
    capabilities = serializers.DictField(child=DCRLiveCapabilitySerializer())
