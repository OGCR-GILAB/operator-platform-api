import uuid

from django.conf import settings
from django.db import models


def document_upload_path(instance, filename: str) -> str:
    """media/documents/<operator or 'unassigned'>/<uuid>.<ext>: never trust the client name."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    scope = instance.operator_id or "unassigned"
    return f"documents/{scope}/{uuid.uuid4().hex}.{ext}"


class Document(models.Model):
    """
    Supporting document for a project or a parcel (ownership proof, cadastral extract,
    methodology, monitoring report, ...). Stored locally in the private media volume and
    served only through the authenticated download endpoint. Versioned: a new upload for
    the same document supersedes the previous one; history is kept.
    """

    class Kind(models.TextChoices):
        OWNERSHIP_PROOF = "ownership_proof", "Proof of ownership"
        LAND_USE_AGREEMENT = "land_use_agreement", "Land use agreement / lease"
        CADASTRAL_EXTRACT = "cadastral_extract", "Cadastral extract"
        METHODOLOGY = "methodology", "Methodology / quantification"
        MONITORING_REPORT = "monitoring_report", "Monitoring report"
        PHOTO = "photo", "Photo"
        MAP = "map", "Map / geodata"
        OTHER = "other", "Other"

    class ForwardStatus(models.TextChoices):
        LOCAL = "local", "Stored locally"
        FORWARDED = "forwarded", "Forwarded"
        FAILED = "failed", "Forwarding failed"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="documents",
    )
    parcel = models.ForeignKey(
        "parcels.Parcel",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="documents",
    )
    operator = models.ForeignKey(
        "operators.Operator",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="documents",
        help_text="Derived from the project or parcel at upload time",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_documents"
    )

    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.OTHER)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    file = models.FileField(upload_to=document_upload_path, max_length=255)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=127, blank=True)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)

    version = models.PositiveIntegerField(default=1)
    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by"
    )
    is_current = models.BooleanField(default=True, db_index=True)

    # forwarding to the external verification platform (target still to be defined)
    forward_status = models.CharField(
        max_length=16, choices=ForwardStatus.choices, default=ForwardStatus.LOCAL
    )
    forwarded_at = models.DateTimeField(null=True, blank=True)
    forward_reference = models.CharField(max_length=255, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.title} v{self.version}"

    @property
    def parent(self):
        return self.project or self.parcel

    @property
    def is_frozen(self) -> bool:
        """Documents of a submitted project or a DCR-registered parcel stay as they are."""
        if self.forward_status == self.ForwardStatus.FORWARDED:
            return True
        if self.project_id and not self.project.is_editable:
            return True
        if self.parcel_id and self.parcel.is_synced:
            return True
        return False
