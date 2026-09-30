from rest_framework import status
from rest_framework.exceptions import APIException


class Conflict(APIException):
    """The request is valid but conflicts with the current state of the resource."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state of the resource."
    default_code = "conflict"


class ServiceUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "An upstream service is unavailable."
    default_code = "service_unavailable"


class BadGateway(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "An upstream service returned an error."
    default_code = "bad_gateway"
