from rest_framework import status
from rest_framework.response import Response

from .exceptions import Conflict


class OneToOneSubresourceMixin:
    """
    GET / PUT / PATCH handling for a one-to-one child of the viewset object,
    e.g. the activity plan of a project or the owner verification of a parcel.
    """

    def one_to_one(self, request, serializer_class, related_name, parent_field, editable=True):
        parent = self.get_object()
        instance = getattr(parent, related_name, None)
        if request.method == "GET":
            if instance is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            return Response(serializer_class(instance).data)
        if not editable:
            raise Conflict("Parent record is no longer editable")
        partial = request.method == "PATCH" and instance is not None
        serializer = serializer_class(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        created = instance is None
        serializer.save(**{parent_field: parent})
        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(serializer.data, status=code)
