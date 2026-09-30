from django.conf import settings
from django.http import JsonResponse


class RequestBodyLimitMiddleware:
    """
    Reject requests whose declared body exceeds the configured limit.

    The body is never handed to Django. Up to twice the limit is read and discarded first,
    so the reverse proxy can deliver a clean 413 to the client (an early close would surface
    as 502); anything larger is refused immediately.
    """

    CHUNK = 1024 * 1024

    def __init__(self, get_response):
        self.get_response = get_response
        self.limit = settings.MAX_REQUEST_BODY_BYTES

    def __call__(self, request):
        length = request.META.get("CONTENT_LENGTH")
        if length and length.isdigit() and int(length) > self.limit:
            declared = int(length)
            if declared <= 2 * self.limit:
                self._drain(request, declared)
            return JsonResponse(
                {"detail": f"Request body exceeds {self.limit // (1024 * 1024)} MB"}, status=413
            )
        return self.get_response(request)

    def _drain(self, request, declared: int) -> None:
        stream = request.META.get("wsgi.input")
        remaining = declared
        try:
            while remaining > 0 and stream is not None:
                chunk = stream.read(min(self.CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except Exception:  # noqa: BLE001 - a broken client stream must not turn into a 500
            pass
