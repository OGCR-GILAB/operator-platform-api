from django.conf import settings
from django.db import models

from apps.core.models import DCRSyncedModel


class Operator(DCRSyncedModel):
    """
    DCR `operator`: the natural or legal person that operates an activity.
    Field names follow the DCR entity so the payload mapping stays 1:1.
    """

    legal_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=64, blank=True)
    address_line_1 = models.CharField(max_length=255, blank=True)
    address_line_2 = models.CharField(max_length=255, blank=True)
    postcode = models.CharField(max_length=32, blank=True)
    country_code = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    ogcr_wallet_address = models.CharField(max_length=128, blank=True)

    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="OperatorMembership", related_name="operators"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_operators",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["legal_name"]

    def __str__(self) -> str:
        return self.legal_name


class OperatorMembership(DCRSyncedModel):
    """DCR `user_operator_relationship`: many-to-many between users and operators."""

    operator = models.ForeignKey(Operator, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="operator_memberships"
    )
    relationship = models.CharField(
        max_length=100,
        default="Employee",
        help_text="How the user relates to the operator, e.g. Managing Director, Employee",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("operator", "user")]
        ordering = ["operator", "user"]

    def __str__(self) -> str:
        return f"{self.user} @ {self.operator} ({self.relationship})"
