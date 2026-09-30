from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.core import reference
from apps.core.models import DCRSyncedModel
from apps.operators.models import Operator


class Project(DCRSyncedModel):
    """
    A carbon removal project prepared on the operator platform.

    Mirrors the DCR `activity` entity (field names match the DCR property list).
    Edited locally while DRAFT/READY; pushed to DCR only on explicit submit.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready for submission"
        SUBMITTED = "submitted", "Submitted to DCR"
        ACCEPTED = "accepted", "Accepted by DCR"
        REJECTED = "rejected", "Rejected by DCR"

    class ActivityType(models.TextChoices):
        CARBON_FARMING = "CARBON_FARMING", "Carbon farming"
        CARBON_STORAGE_IN_PRODUCTS = "CARBON_STORAGE_IN_PRODUCTS", "Carbon storage in products"
        PERMANENT_REMOVAL = "PERMANENT_REMOVAL", "Permanent carbon removal"

    EDITABLE_STATUSES = (Status.DRAFT, Status.READY)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="projects"
    )
    operator = models.ForeignKey(
        Operator, on_delete=models.PROTECT, related_name="projects", null=True, blank=True
    )

    # --- activity fields (DCR property names)
    name = models.CharField(max_length=255)
    summary = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    website = models.URLField(blank=True)
    image = models.URLField(blank=True)
    hero_image = models.URLField(blank=True)
    media_links = models.JSONField(default=list, blank=True, help_text="List of URLs")
    technologies_practices_processes = models.JSONField(
        default=list, blank=True, help_text="List of practice/process identifiers"
    )
    type = models.CharField(
        max_length=40, choices=ActivityType.choices, default=ActivityType.CARBON_FARMING
    )
    unit_types = models.CharField(
        max_length=100,
        blank=True,
        choices=[(u, u) for u in reference.UNIT_TYPES],
        help_text="Unit category produced by the activity; determines type",
    )
    city = models.CharField(max_length=120, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    geometry = gis_models.MultiPolygonField(srid=4326, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    term_commitment = models.PositiveIntegerField(null=True, blank=True, help_text="Years")
    cobenefits = models.JSONField(default=list, blank=True)
    methodologies = models.TextField(blank=True)
    monitoring_period_years = models.PositiveIntegerField(null=True, blank=True)
    monitoring_period_start_date = models.DateField(null=True, blank=True)
    monitoring_period_end_date = models.DateField(null=True, blank=True)
    certification_scheme_id = models.CharField(max_length=128, blank=True)
    certification_scheme_name = models.CharField(max_length=255, blank=True)

    # --- verification outcome mirrored from DCR (see services.refresh_dcr_status)
    dcr_verification = models.JSONField(
        default=dict, blank=True, help_text="Latest activity_verification record from DCR"
    )
    dcr_status_checked_at = models.DateTimeField(null=True, blank=True)

    # --- local lifecycle
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"

    @property
    def is_editable(self) -> bool:
        return self.status in self.EDITABLE_STATUSES


class ActivityPlan(DCRSyncedModel):
    """DCR `activity_plan`: the plan describing a previously created activity."""

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="plan")
    date_submitted = models.DateField(null=True, blank=True)
    activity_eligibility = models.TextField(blank=True)
    legal_parcel_ownership = models.TextField(blank=True)
    coordinate_reference_system = models.CharField(max_length=32, default="EPSG:4326")
    iacs_codes = models.JSONField(default=list, blank=True)
    lpis_codes = models.JSONField(default=list, blank=True)
    article_8_1_information = models.TextField(blank=True)
    methodology_quantification_baseline = models.TextField(blank=True)
    methodology_additionality_funding_sources = models.TextField(blank=True)
    methodology_long_term_storage = models.TextField(blank=True)
    methodology_sustainability = models.TextField(blank=True)
    expected_total_carbon_removals = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True, help_text="tCO2e"
    )
    expected_total_soil_emissions = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True, help_text="tCO2e"
    )
    expected_total_ghg_emissions_associated = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True, help_text="tCO2e"
    )
    expected_net_benefit = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True, help_text="tCO2e"
    )
    group_advisory_services_description = models.TextField(blank=True)
    group_internal_control_system_description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Plan of {self.project}"


class MonitoringPlan(DCRSyncedModel):
    """
    DCR `monitoring_plan`. DCR keeps no foreign key from it to the activity;
    the link lives only here (one plan per project).
    """

    project = models.OneToOneField(
        Project, on_delete=models.CASCADE, related_name="monitoring_plan"
    )
    date_submitted = models.DateField(null=True, blank=True)
    monitored_data_parameters = models.JSONField(
        default=list, blank=True, help_text="List of {name, unit, scope}"
    )
    monitoring_frequency = models.PositiveIntegerField(null=True, blank=True, help_text="Months")
    emission_sources_and_sinks = models.TextField(blank=True)
    data_source = models.TextField(blank=True)
    measurement_methods_procedures_accuracy_calibration = models.TextField(blank=True)
    quality_assessment_or_quality_control_procedures = models.TextField(blank=True)
    responsibility_for_collection_and_archiving = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Monitoring plan of {self.project}"
