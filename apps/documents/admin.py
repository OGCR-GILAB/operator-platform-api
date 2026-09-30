from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Document


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = (
        "title",
        "kind",
        "version",
        "is_current",
        "project",
        "parcel",
        "size",
        "forward_status",
        "uploaded_by",
        "uploaded_at",
    )
    list_filter = ("kind", "is_current", "forward_status")
    search_fields = ("title", "original_name", "sha256")
    readonly_fields = ("sha256", "size", "content_type", "original_name", "uploaded_at")
    autocomplete_fields = ("project", "parcel", "uploaded_by")
