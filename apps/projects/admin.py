from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from unfold.admin import ModelAdmin, StackedInline

from .models import ActivityPlan, MonitoringPlan, Project


class ActivityPlanInline(StackedInline):
    model = ActivityPlan
    extra = 0


class MonitoringPlanInline(StackedInline):
    model = MonitoringPlan
    extra = 0


@admin.register(Project)
class ProjectAdmin(GISModelAdmin, ModelAdmin):
    list_display = (
        "name",
        "operator",
        "owner",
        "status",
        "dcr_id",
        "dcr_sync_status",
        "updated_at",
    )
    list_filter = ("status", "dcr_sync_status", "type")
    search_fields = ("name", "dcr_id", "owner__username", "operator__legal_name")
    readonly_fields = (
        "created_at",
        "updated_at",
        "submitted_at",
        "dcr_synced_at",
        "dcr_response",
        "dcr_verification",
        "dcr_status_checked_at",
    )
    autocomplete_fields = ("owner", "operator")
    inlines = [ActivityPlanInline, MonitoringPlanInline]


@admin.register(ActivityPlan)
class ActivityPlanAdmin(ModelAdmin):
    list_display = ("project", "expected_net_benefit", "dcr_sync_status", "updated_at")
    search_fields = ("project__name",)
    readonly_fields = ("dcr_synced_at", "dcr_response", "created_at", "updated_at")


@admin.register(MonitoringPlan)
class MonitoringPlanAdmin(ModelAdmin):
    list_display = ("project", "monitoring_frequency", "dcr_sync_status", "updated_at")
    search_fields = ("project__name",)
    readonly_fields = ("dcr_synced_at", "dcr_response", "created_at", "updated_at")
