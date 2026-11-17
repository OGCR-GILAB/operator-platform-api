from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.core.models import DCRSyncedModel
from apps.operators.models import Operator

EQUAL_AREA_SRID = 3035  # ETRS89 / LAEA Europe, used only to compute areas


class VerificationStatus(models.TextChoices):
    # the certification platform's codes (draft, under_review, validated, denied, in_progress,
    # verified, failed); pending is kept for older records
    DRAFT = "draft", "Draft"
    PENDING = "pending", "Pending"
    UNDER_REVIEW = "under_review", "Under review"
    VALIDATED = "validated", "Validated"
    DENIED = "denied", "Denied"
    IN_PROGRESS = "in_progress", "In progress"
    VERIFIED = "verified", "Verified"
    FAILED = "failed", "Failed"
    VALIDATION_AMENDMENT_REQUIRED = (
        "validation_amendment_required",
        "Amendment required (validation)",
    )
    VERIFICATION_AMENDMENT_REQUIRED = (
        "verification_amendment_required",
        "Amendment required (verification)",
    )


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
    def accepts_documents(self) -> bool:
        """While any activity it belongs to still accepts documents (or it belongs to none)."""
        projects = [link.project for link in self.project_links.select_related("project")]
        return not projects or any(p.accepts_documents for p in projects)

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
        max_length=40, choices=VerificationStatus.choices, default=VerificationStatus.IN_PROGRESS
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


class ParcelOwner(DCRSyncedModel):
    """
    DCR `parcel_owner`: the owner as declared by the operator, with the deed or cadastre
    extract it rests on. Sent once the certification platform has registered the parcel on
    DCR (its record is what `parcel_id` points to); the certifier verifies it separately
    (`parcel_owner_verification`).
    """

    class OwnerType(models.TextChoices):
        NATURAL_PERSON = "natural_person", "Natural person"
        LEGAL_PERSON = "legal_person", "Legal person"
        PUBLIC_ENTITY = "public_entity", "Public entity"

    parcel = models.OneToOneField(Parcel, on_delete=models.CASCADE, related_name="owner")
    legal_name = models.CharField(max_length=255)
    owner_type = models.CharField(max_length=20, choices=OwnerType.choices)
    country_code = models.CharField(max_length=2, help_text="Country of the owner's address")
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Deed or cadastre extract of this parcel",
    )
    supporting_document_dcr_id = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Owner of {self.parcel}: {self.legal_name}"

    @property
    def is_complete(self) -> bool:
        return bool(self.legal_name and self.owner_type and self.country_code and self.document)


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
        max_length=40, choices=VerificationStatus.choices, default=VerificationStatus.IN_PROGRESS
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


class RegistryOwner(models.Model):
    """
    A right on a French parcel held by a legal person, as published by the DGFiP (see
    apps.parcels.registry). Keyed by the cadastral id, not linked to Parcel: the whole
    country is loaded so any parcel, drawn now or later, is matched by its reference.
    Reference data, reloaded every year; it can be left out of database backups.
    """

    idu = models.CharField(max_length=14, db_index=True)
    right_code = models.CharField(
        max_length=2, help_text="P owner, U usufructuary, N bare owner, ..."
    )
    right_label = models.CharField(max_length=80, blank=True)
    siren = models.CharField(max_length=9, blank=True)
    legal_name = models.CharField(max_length=255)
    legal_form = models.CharField(max_length=16, blank=True, help_text="e.g. SCI, GFA, COM")
    legal_form_code = models.CharField(max_length=8, blank=True)
    person_group = models.CharField(max_length=80, blank=True, help_text="e.g. Commune, State")
    vintage = models.PositiveSmallIntegerField(help_text="Situation on 1 January of this year")

    def __str__(self) -> str:
        return f"{self.idu} {self.right_code} {self.legal_name}"
