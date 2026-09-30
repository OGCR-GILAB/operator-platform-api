from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Operator, OperatorMembership


class MembershipInline(TabularInline):
    model = OperatorMembership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Operator)
class OperatorAdmin(ModelAdmin):
    list_display = (
        "legal_name",
        "email",
        "country_code",
        "dcr_id",
        "dcr_sync_status",
        "updated_at",
    )
    list_filter = ("dcr_sync_status", "country_code")
    search_fields = ("legal_name", "email", "dcr_id")
    readonly_fields = ("dcr_synced_at", "dcr_response", "created_at", "updated_at")
    inlines = [MembershipInline]
