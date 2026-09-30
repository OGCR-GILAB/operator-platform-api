"""
Errors raised by the DCR client and their mapping to HTTP responses.
"""


class DCRError(Exception):
    """Base error for communication with the DCR platform."""

    default_message = "DCR platform error"

    def __init__(self, message: str | None = None, status_code: int | None = None, payload=None):
        super().__init__(message or self.default_message)
        self.status_code = status_code
        self.payload = payload


class DCRConfigurationError(DCRError):
    default_message = "DCR client is not configured (DCR_BASE_URL / DCR_CONSUMER_KEY)"


class DCRAuthError(DCRError):
    default_message = "DCR rejected the credentials or token"


class DCRUnavailable(DCRError):
    default_message = "DCR platform is unreachable"


def dcr_exception_handler(exc, context):
    """DRF exception handler that translates DCR errors into API responses."""
    # imported lazily: this module is loaded while DRF settings are still being resolved
    from rest_framework.response import Response
    from rest_framework.views import exception_handler

    from apps.core.exceptions import BadGateway, ServiceUnavailable

    if isinstance(exc, DCRAuthError):
        # Returned directly so DRF does not downgrade 401 -> 403 on views without auth.
        return Response({"detail": str(exc), "code": "dcr_auth_failed"}, status=401)
    if isinstance(exc, DCRUnavailable | DCRConfigurationError):
        exc = ServiceUnavailable(str(exc))
    elif isinstance(exc, DCRError) and exc.status_code in (400, 409):
        # validation errors and conflicts from DCR are the caller's to fix
        return Response({"detail": str(exc), "code": "dcr_rejected"}, status=exc.status_code)
    elif isinstance(exc, DCRError):
        exc = BadGateway(str(exc))
    return exception_handler(exc, context)
