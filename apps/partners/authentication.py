from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from .models import PartnerApiKey


class PartnerApiKeyAuthentication(BaseAuthentication):
    """Authorization: Api-Key <key>  (partner platforms, not end users)."""

    keyword = "Api-Key"

    def authenticate(self, request):
        header = get_authorization_header(request).split(None, 1)
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise AuthenticationFailed("Invalid Api-Key header")
        key = PartnerApiKey.authenticate(header[1].decode("utf-8", "ignore").strip())
        if key is None:
            raise AuthenticationFailed("Invalid or revoked API key")
        return (key, key)

    def authenticate_header(self, request):
        return self.keyword


class IsPartner(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, PartnerApiKey)


class PartnerApiKeyScheme(OpenApiAuthenticationExtension):
    target_class = "apps.partners.authentication.PartnerApiKeyAuthentication"
    name = "PartnerApiKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Partner key, e.g. `Api-Key ogcr_...`",
        }
