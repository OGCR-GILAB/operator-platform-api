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

    def validate(self, attrs):
        attrs["username"] = (attrs.get("username") or attrs["email"]).strip()
        return attrs


class RegisterResponseSerializer(serializers.Serializer):
    user = UserSerializer()
    detail = serializers.CharField()


class EmailValidationSerializer(serializers.Serializer):
    token = serializers.CharField()


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()
    username = serializers.CharField(required=False, allow_blank=True)
