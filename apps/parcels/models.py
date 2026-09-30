from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.core.models import DCRSyncedModel
from apps.operators.models import Operator

EQUAL_AREA_SRID = 3035  # ETRS89 / LAEA Europe, used only to compute areas


class VerificationStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    VERIFIED = "verified", "Verified"
    FAILED = "failed", "Failed"


class Parcel(DCRSyncedModel):
    """
    DCR `parcel`: a geospatially defined unit of land. Belongs to an operator and can
    take part in several projects (see ProjectParcel). Geometry is stored in EPSG:4326.
    """

    operator = models.ForeignKey(Operator, on_delete=models.PROTECT, related_name="parcels")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_parcels",
    )
    name = models.CharField(max_length=255, blank=True, help_text="Local label shown in the UI")
    cadastral_reference = models.CharField(
        max_length=64, blank=True, db_index=True, help_text="Cadastre identifier (e.g. French IDU)"
    )
    geometry = gis_models.MultiPolygonField(srid=4326)
    iacs_codes = models.JSONField(default=list, blank=True)
    lpis_codes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.name or self.cadastral_reference or f"parcel {self.pk}"

    @property
    def area_ha(self) -> float | None:
        if self.geometry is None:
            return None
        return round(self.geometry.transform(EQUAL_AREA_SRID, clone=True).area / 10_000, 4)


class ParcelOwnerVerification(DCRSyncedModel):
    """DCR `parcel_owner_verification`: outcome of verifying who owns the parcel."""

    parcel = models.OneToOneField(
        Parcel, on_delete=models.CASCADE, related_name="owner_verification"
    )
    status_code = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.IN_PROGRESS
    )
    status_message = models.CharField(max_length=500, blank=True)
    parcel_owner_legal_name = models.CharField(max_length=255, blank=True)
    authority = models.CharField(
        max_length=255, blank=True, help_text="Authority that verified the ownership"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Owner verification of {self.parcel} ({self.status_code})"


class ProjectParcel(DCRSyncedModel):
    """
    DCR `activity_parcel_verification`: the link between a parcel and a project
    (activity) together with its verification state.
    """

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="parcel_links"
    )
    parcel = models.ForeignKey(Parcel, on_delete=models.PROTECT, related_name="project_links")
    status_code = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.IN_PROGRESS
    )
    status_message = models.CharField(max_length=500, blank=True)
    amount = models.IntegerField(
        null=True, blank=True, help_text="Carbon reduction calculated for this parcel (tCO2e)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("project", "parcel")]
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.parcel} in {self.project} ({self.status_code})"
