import logging

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
from .reference import certification_schemes, password_policy
from .roles import capabilities, live_permissions
from .serializers import (
    CertificationSchemeSerializer,
    DCRPermissionsSerializer,
    EmailValidationSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    MobilePhoneSerializer,
    PasswordPolicySerializer,
    PasswordResetSerializer,
    RegisterResponseSerializer,
    RegisterSerializer,
    ResendValidationEmailSerializer,
)

logger = logging.getLogger(__name__)


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
            mobile_phone_number=data.get("mobile_phone_number", ""),
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
        username = serializer.validated_data.get("username") or ""
        client = get_client()
        client.service_token()  # a missing service account is a configuration error (503)
        # DCR needs username, e-mail and the user's id next to each other
        accounts = _dcr_accounts(client, email, username)
        if not accounts:
            logger.info("password reset: no DCR user for the given e-mail")
        for account in accounts[:MAX_RESET_ACCOUNTS]:
            try:
                answer = client.send_password_reset(*account)
            except DCRAuthError:
                raise  # service account problem: surface it (503 via the handler is misleading)
            except DCRError as exc:
                if exc.status_code not in (400, 404):
                    raise
                logger.info("password reset for %s refused by DCR: %s", account[0], exc)
            else:
                status_ = answer.get("status") if isinstance(answer, dict) else answer
                logger.info("password reset for %s: DCR answered %s", account[0], status_)
        return Response(PASSWORD_RESET_SENT, status=status.HTTP_202_ACCEPTED)


PASSWORD_RESET_SENT = {"detail": "If the account exists, DCR has sent a password reset e-mail."}
MAX_RESET_ACCOUNTS = 3  # one e-mail address can belong to several DCR usernames


def _dcr_accounts(client, email: str, username: str) -> list[tuple[str, str, str]]:
    """
    (username, e-mail, user id) of the DCR users with this e-mail, and this username when
    given. Looked up on DCR (CanGetAnyUser); if that is not possible, from the users who
    registered or signed in here.
    """
    try:
        users = client.users_by_email(email)
    except DCRAuthError:
        raise
    except DCRError as exc:
        if exc.status_code == 404:
            return []
        logger.warning("password reset: DCR user lookup failed (%s), using local users", exc)
        return _local_accounts(email, username)
    accounts = []
    for user in users:
        name, address, user_id = user.get("username"), user.get("email"), user.get("user_id")
        if not (name and user_id) or (address or "").lower() != email.lower():
            continue
        if username and name.lower() != username.lower():
            continue
        accounts.append((name, address, user_id))
    return accounts


def _local_accounts(email: str, username: str) -> list[tuple[str, str, str]]:
    """The local mirror of DCR users by e-mail (and username when given), latest sign-in first."""
    from apps.accounts.models import User

    users = User.objects.exclude(dcr_user_id__isnull=True).exclude(dcr_user_id="")
    users = users.filter(email__iexact=email)
    if username:
        users = users.filter(username__iexact=username)
    return [(u.username, u.email or email, u.dcr_user_id) for u in users.order_by("-last_login")]


class ResendValidationEmailView(APIView):
    """
    Ask DCR to send the e-mail validation link again (DCR v7.0.0+). Always answers 202,
    so account existence is not leaked.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=ResendValidationEmailSerializer, responses={202: None})
    def post(self, request):
        serializer = ResendValidationEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        username = serializer.validated_data.get("username") or email
        try:
            get_client().resend_validation_email(username, email)
        except DCRAuthError:
            raise
        except DCRError as exc:
            if exc.status_code not in (400, 404):
                raise
        return Response(
            {"detail": "If an unvalidated account exists, DCR has sent a new validation e-mail."},
            status=status.HTTP_202_ACCEPTED,
        )


class PasswordPolicyView(APIView):
    """The password rules DCR enforces at registration (cached). Public."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(responses={200: PasswordPolicySerializer, 503: None})
    def get(self, request):
        policy = password_policy()
        if policy is None:
            return Response(
                {"detail": "DCR password policy is currently unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(policy)


class DCRPermissionsView(APIView):
    """
    The DCR roles the platform needs, checked one by one on DCR (bank-level roles at
    DCR_BANK_ID), and what they allow: submit, status (verification results) and
    reference data. Cached for 5 minutes; `?refresh=1` checks again. Falls back to the
    login-time role list (`source: "profile"`) when DCR cannot answer.
    """

    @extend_schema(responses={200: DCRPermissionsSerializer})
    def get(self, request):
        token = request.auth if isinstance(request.auth, str) else None
        result = None
        if token:
            refresh = request.query_params.get("refresh") in ("1", "true")
            result = live_permissions(token, use_cache=not refresh)
        if result is not None:
            return Response({"source": "live", **result})
        from django.conf import settings

        return Response(
            {
                "source": "profile",
                "bank_id": settings.DCR.get("BANK_ID") or "",
                "checked_at": None,
                "roles": [],
                "capabilities": capabilities(request.user.dcr_profile or {}),
            }
        )


class MobilePhoneView(APIView):
    """Set the user's mobile phone number on DCR (DCR v7.0.0+) and return the refreshed user."""

    @extend_schema(request=MobilePhoneSerializer, responses={200: MeSerializer})
    def put(self, request):
        serializer = MobilePhoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = request.auth if isinstance(request.auth, str) else None
        if not token:
            return Response(
                {"detail": "A DCR token is required", "code": "dcr_auth_failed"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        client = get_client()
        client.update_mobile_phone_number(token, serializer.validated_data["mobile_phone_number"])
        user = sync_user_from_dcr(client.current_user(token))
        return Response(MeSerializer(user).data)
