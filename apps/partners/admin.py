from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import PartnerApiKey


@admin.register(PartnerApiKey)
class PartnerApiKeyAdmin(ModelAdmin):
    list_display = ("name", "prefix", "is_active", "created_at", "last_used_at")
    list_filter = ("is_active",)
    readonly_fields = ("prefix", "key_hash", "created_at", "last_used_at")
    fields = ("name", "is_active", "notes", "prefix", "key_hash", "created_at", "last_used_at")
