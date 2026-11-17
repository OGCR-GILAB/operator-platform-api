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
    min_overlap = serializers.FloatField(
        default=0.01,
        min_value=0,
        max_value=1,
        help_text=(
            "Share of the smaller of the two areas (parcel or search geometry) that must be "
            "covered by their overlap. The default leaves out neighbouring parcels that only "
            "touch the geometry or overlap it by a sliver; 0 returns every intersecting parcel."
        ),
    )
