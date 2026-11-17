from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import PartnerTrack, Release, RoadmapItem


@admin.register(Release)
class ReleaseAdmin(ModelAdmin):
    list_display = ("released_on", "title", "build", "issues", "published")
    list_filter = ("published",)
    search_fields = ("title", "summary", "build")
    date_hierarchy = "released_on"


@admin.register(RoadmapItem)
class RoadmapItemAdmin(ModelAdmin):
    list_display = ("title", "area", "status", "target", "position", "updated_at")
    list_editable = ("status", "position")
    list_filter = ("status", "area")
    search_fields = ("title", "description")


@admin.register(PartnerTrack)
class PartnerTrackAdmin(ModelAdmin):
    list_display = ("partner", "topic", "status", "next_step", "updated_on")
    list_editable = ("status",)
    list_filter = ("partner", "status")
    search_fields = ("topic", "detail", "next_step")
