from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "role", "first_name", "last_name", "is_active", "is_staff"]
    list_filter = ["role", "is_active", "is_staff"]
    search_fields = ["email", "first_name", "last_name"]
    fieldsets = [
        (None, {"fields": ["email", "password", "role"]}),
        ("Personal info", {"fields": ["first_name", "last_name"]}),
        (
            "Permissions",
            {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]},
        ),
        ("Dates", {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [
        (None, {"classes": ["wide"], "fields": ["email", "role", "password1", "password2"]}),
    ]
