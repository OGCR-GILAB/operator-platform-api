from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "email", "dcr_user_id", "dcr_provider", "is_staff", "is_active")
    list_filter = ("is_staff", "is_active", "dcr_provider")
    search_fields = ("username", "email", "dcr_user_id")
    readonly_fields = ("dcr_synced_at",)
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("DCR", {"fields": ("dcr_user_id", "dcr_provider", "dcr_synced_at")}),
    )
