from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.serializers import MeSerializer, UserSerializer
from apps.accounts.services import sync_user_from_dcr

from .authentication import forget_token, remember_token
from .client import get_client
from .exceptions import DCRAuthError, DCRError
from .reference import certification_schemes
from .serializers import (
    CertificationSchemeSerializer,
    EmailValidationSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    PasswordResetSerializer,
    RegisterResponseSerializer,
    RegisterSerializer,
)


class LoginView(APIView):
    """
    Proxy for DCR DirectLogin.

    The consumer key stays on the server; the frontend only ever sees the user token.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses={200: LoginResponseSerializer})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        client = get_client()
        token = client.direct_login(
            serializer.validated_data["username"], serializer.validated_data["password"]
        )
        profile = client.current_user(token)
        user = sync_user_from_dcr(profile)
        remember_token(token, user)

        return Response({"token": token, "user": UserSerializer(user).data})


class LogoutView(APIView):
    """Drops the cached token -> user mapping. The DCR token itself stays valid on DCR."""

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        if isinstance(request.auth, str):
            forget_token(request.auth)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """
    The current user, with a `dcr` block: the account as DCR knows it, the roles it holds
    and whether they are sufficient to submit, read verification status and reference data.
    """

    @extend_schema(responses={200: MeSerializer})
    def get(self, request):
        return Response(MeSerializer(request.user).data)


class CertificationSchemeListView(APIView):
    """Certification schemes available on DCR (read-only reference data, cached)."""

    @extend_schema(responses={200: CertificationSchemeSerializer(many=True)})
    def get(self, request):
        schemes = certification_schemes(request.auth)
        return Response(CertificationSchemeSerializer(schemes, many=True).data)


class RegisterView(APIView):
    """
    Register the user on DCR (the platform of record for accounts) and mirror it locally.

    DCR may require e-mail validation before the first login; the frontend should then
    send the token from the e-mail to /api/auth/validate-email/.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    @extend_schema(request=RegisterSerializer, responses={201: RegisterResponseSerializer})
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        profile = get_client().create_user(
            email=data["email"],
            username=data["username"],
            password=data["password"],
            first_name=data["first_name"],
            last_name=data["last_name"],
        )
        user = sync_user_from_dcr(profile)
        if not user.first_name:
            user.first_name, user.last_name = data["first_name"], data["last_name"]
            user.save(update_fields=["first_name", "last_name"])
        return Response(
            {
                "user": UserSerializer(user).data,
                "detail": "Registered on DCR. Validate the e-mail if DCR sent a link, then log in.",
            },
            status=status.HTTP_201_CREATED,
        )


class ValidateEmailView(APIView):
    """Confirm the e-mail address with the token DCR sent after registration."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(request=EmailValidationSerializer, responses={200: None})
    def post(self, request):
        serializer = EmailValidationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            get_client().validate_email(serializer.validated_data["token"])
        except DCRError as exc:
            if exc.status_code == 404:
                return Response(
                    {"detail": "Validation token is invalid or expired", "code": "dcr_rejected"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise
        return Response({"detail": "E-mail address validated. You can log in now."})


class PasswordResetView(APIView):
    """
    Ask DCR to e-mail a password reset link. Requires the DCR service account
    (DCR_SERVICE_USERNAME / DCR_SERVICE_PASSWORD with the CanCreateResetPasswordUrl role).
    Always answers 202 so account existence is not leaked.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=PasswordResetSerializer, responses={202: None})
    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        username = serializer.validated_data.get("username") or email
        try:
            get_client().send_password_reset(username, email)
        except DCRAuthError:
            raise  # service account problem: surface it (503 via the handler is misleading)
        except DCRError as exc:
            if exc.status_code not in (400, 404):
                raise
        return Response(
            {"detail": "If the account exists, DCR has sent a password reset e-mail."},
            status=status.HTTP_202_ACCEPTED,
        )
