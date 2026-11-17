import uuid

from django.conf import settings
from django.db import models


def new_dcr_id(prefix: str) -> str:
    """`<prefix>_<random>`, behind DCR_ID_PREFIX when one is set (test setups)."""
    dcr_id = f"{prefix}_{uuid.uuid4().hex[:24]}"
    common = settings.DCR.get("ID_PREFIX")
    return f"{common}_{dcr_id}" if common else dcr_id


class DCRSyncedModel(models.Model):
    """
    Mixin for records that mirror a DCR dynamic entity.

    Everything is authored locally; a record only gets a `dcr_id` after the user
    explicitly submits it (see apps.projects.services.submit_project).
    """

    class SyncStatus(models.TextChoices):
        LOCAL = "local", "Local only"
        SYNCED = "synced", "Synced with DCR"
        FAILED = "failed", "Last sync failed"

    dcr_id = models.CharField(max_length=128, blank=True, db_index=True)
    dcr_sync_status = models.CharField(
        max_length=16, choices=SyncStatus.choices, default=SyncStatus.LOCAL
    )
    dcr_synced_at = models.DateTimeField(null=True, blank=True)
    dcr_response = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True

    @property
    def is_synced(self) -> bool:
        return bool(self.dcr_id) and self.dcr_sync_status == self.SyncStatus.SYNCED

    def ensure_dcr_id(self, prefix: str) -> str:
        """
        DCR expects the client to supply entity ids; generate one once and keep it.
        DCR_ID_PREFIX (empty in production) marks ids from a test setup, e.g.
        `OPP-TEST_act_...`, so a partner test instance can pick out its records.
        """
        if not self.dcr_id:
            self.dcr_id = new_dcr_id(prefix)
        return self.dcr_id
