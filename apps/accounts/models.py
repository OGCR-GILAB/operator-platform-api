from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Local mirror of a DCR platform user.

    Registration and login happen on the DCR platform; a local row is created the
    first time a valid DCR token is seen so that projects can reference an owner.
    """

    dcr_user_id = models.CharField(
        max_length=128,
        unique=True,
        null=True,
        blank=True,
        help_text="user_id on the DCR platform",
    )
    dcr_provider = models.CharField(max_length=255, blank=True)
    dcr_synced_at = models.DateTimeField(null=True, blank=True)
    dcr_profile = models.JSONField(
        default=dict,
        blank=True,
        help_text="Last /users/current payload from DCR (incl. entitlements)",
    )

    class Meta:
        ordering = ["username"]

    def __str__(self) -> str:
        return self.username
