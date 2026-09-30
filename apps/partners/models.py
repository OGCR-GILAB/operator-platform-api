import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class PartnerApiKey(models.Model):
    """
    API key for a partner platform (e.g. the verification platform working with auditors).
    Only the SHA-256 of the key is stored; the clear key is shown once at creation.
    """

    name = models.CharField(max_length=120, unique=True)
    prefix = models.CharField(max_length=12, db_index=True, help_text="First characters of the key")
    key_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}...)"

    # DRF treats the key as the request principal
    is_authenticated = True
    is_anonymous = False
    is_staff = False
    is_superuser = False

    @staticmethod
    def hash_key(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def generate(cls, name: str, created_by=None, notes: str = "") -> tuple["PartnerApiKey", str]:
        raw = "ogcr_" + secrets.token_hex(24)
        key = cls.objects.create(
            name=name,
            prefix=raw[:12],
            key_hash=cls.hash_key(raw),
            created_by=created_by,
            notes=notes,
        )
        return key, raw

    @classmethod
    def authenticate(cls, raw: str) -> "PartnerApiKey | None":
        key = cls.objects.filter(key_hash=cls.hash_key(raw), is_active=True).first()
        if key is not None:
            cls.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return key
