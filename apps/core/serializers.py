from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from rest_framework import serializers
from rest_framework_gis.fields import GeometryField


class MultiPolygonGeometryField(GeometryField):
    """
    GeoJSON in/out. A single Polygon is promoted to a MultiPolygon and any input
    SRID is transformed to EPSG:4326, which is what we store and send to DCR.
    """

    def to_internal_value(self, value):
        geometry = super().to_internal_value(value)
        if geometry is None:
            return None
        if isinstance(geometry, Polygon):
            geometry = MultiPolygon(geometry)
        if not isinstance(geometry, MultiPolygon):
            raise serializers.ValidationError("Geometry must be a Polygon or MultiPolygon")
        if geometry.srid is None:
            geometry.srid = 4326
        elif geometry.srid != 4326:
            geometry = GEOSGeometry(geometry.wkt, srid=geometry.srid)
            geometry.transform(4326)
        if geometry.num_coords > settings.MAX_GEOMETRY_VERTICES:
            raise serializers.ValidationError(
                f"Geometry has too many vertices (max {settings.MAX_GEOMETRY_VERTICES})"
            )
        if not geometry.valid:
            raise serializers.ValidationError(f"Invalid geometry: {geometry.valid_reason}")
        return geometry
