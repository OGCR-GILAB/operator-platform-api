from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from unfold.admin import ModelAdmin, StackedInline, TabularInline

from .models import Parcel, ParcelOwnerVerification, ProjectParcel


class OwnerVerificationInline(StackedInline):
    model = ParcelOwnerVerification
    extra = 0


class ProjectParcelInline(TabularInline):
    model = ProjectParcel
    extra = 0
    autocomplete_fields = ("project",)


@admin.register(Parcel)
class ParcelAdmin(GISModelAdmin, ModelAdmin):
    list_display = (
        "__str__",
        "operator",
        "cadastral_reference",
        "dcr_id",
        "dcr_sync_status",
        "updated_at",
    )
    list_filter = ("dcr_sync_status", "operator")
    search_fields = ("name", "cadastral_reference", "dcr_id")
    readonly_fields = ("dcr_synced_at", "dcr_response", "created_at", "updated_at")
    autocomplete_fields = ("operator",)
    inlines = [OwnerVerificationInline, ProjectParcelInline]


@admin.register(ProjectParcel)
class ProjectParcelAdmin(ModelAdmin):
    list_display = ("parcel", "project", "status_code", "amount", "dcr_sync_status")
    list_filter = ("status_code", "dcr_sync_status")
    search_fields = ("parcel__name", "parcel__cadastral_reference", "project__name")
    autocomplete_fields = ("parcel", "project")


@admin.register(ParcelOwnerVerification)
class ParcelOwnerVerificationAdmin(ModelAdmin):
    list_display = (
        "parcel",
        "status_code",
        "authority",
        "parcel_owner_legal_name",
        "dcr_sync_status",
    )
    list_filter = ("status_code", "dcr_sync_status")
    search_fields = ("parcel__name", "parcel__cadastral_reference", "parcel_owner_legal_name")
