"""
DRF authentication backed by the DCR platform.

The frontend obtains a DirectLogin token (either directly from DCR or through our
/api/auth/login/ proxy) and sends it as:

    Authorization: DirectLogin token="<token>"

We validate it against DCR (/users/current), mirror the user locally and cache the
token -> user mapping for DCR_AUTH_CACHE_SECONDS so DCR is not hit on every request.
`request.auth` holds the raw token so views can call DCR on behalf of the user.
"""

import hashlib
import re

from django.conf import settings
from django.core.cache import cache
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import User
from apps.accounts.services import sync_user_from_dcr

from .client import get_client
from .exceptions import DCRAuthError

BAD_HEADER = 'Invalid DirectLogin header. Expected: DirectLogin token="<token>"'
TOKEN_RE = re.compile(r"token\s*=\s*\"?([^\",\s]+)\"?", re.IGNORECASE)


def cache_key_for_token(token: str) -> str:
    return "dcr:token:" + hashlib.sha256(token.encode()).hexdigest()


def remember_token(token: str, user: User) -> None:
    cache.set(cache_key_for_token(token), user.pk, settings.DCR["AUTH_CACHE_SECONDS"])


def forget_token(token: str) -> None:
    cache.delete(cache_key_for_token(token))


class DCRDirectLoginAuthentication(BaseAuthentication):
    keyword = "DirectLogin"

    def authenticate(self, request):
        header = get_authorization_header(request).split(None, 1)
        if not header:
            return None
        scheme = header[0].lower()
        if scheme == b"bearer":
            # convenience form (Swagger UI, curl): Authorization: Bearer <token>
            if len(header) == 1 or not header[1].strip():
                raise AuthenticationFailed("Invalid Bearer header: token missing")
            token = header[1].decode("utf-8", "ignore").strip().strip(chr(34))
            return (self.resolve_user(token), token)
        if scheme != self.keyword.lower().encode():
            return None
        if len(header) == 1:
            raise AuthenticationFailed(BAD_HEADER)

        match = TOKEN_RE.search(header[1].decode("utf-8", "ignore"))
        if not match:
            raise AuthenticationFailed(BAD_HEADER)
        token = match.group(1)
        return (self.resolve_user(token), token)

    def resolve_user(self, token: str) -> User:
        key = cache_key_for_token(token)
        user_id = cache.get(key)
        if user_id is not None:
            user = User.objects.filter(pk=user_id, is_active=True).first()
            if user is not None:
                return user

        try:
            profile = get_client().current_user(token)
        except DCRAuthError as exc:
            raise AuthenticationFailed("Invalid or expired DCR token") from exc
        # DCRUnavailable / DCRConfigurationError propagate -> 503 via dcr_exception_handler

        user = sync_user_from_dcr(profile)
        if not user.is_active:
            raise AuthenticationFailed("User account is disabled")
        remember_token(token, user)
        return user

    def authenticate_header(self, request):
        return 'DirectLogin realm="dcr"'
