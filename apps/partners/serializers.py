from rest_framework import serializers

from apps.core.serializers import MultiPolygonGeometryField


class ParcelSearchSerializer(serializers.Serializer):
    geometry = MultiPolygonGeometryField(
        help_text="GeoJSON Polygon or MultiPolygon; parcels intersecting it are returned"
    )
    include_unsubmitted = serializers.BooleanField(
        default=False,
        help_text="Also return parcels that are not part of any submitted project",
    )
    include_documents = serializers.BooleanField(default=True)
