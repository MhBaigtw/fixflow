from django.contrib import admin

from .models import Property, Tenancy, Unit


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ["name", "address", "manager", "created_at"]
    search_fields = ["name", "address"]


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["label", "property"]
    list_filter = ["property"]


@admin.register(Tenancy)
class TenancyAdmin(admin.ModelAdmin):
    list_display = ["tenant", "unit", "start_date", "end_date"]
    list_filter = ["end_date"]
    search_fields = ["tenant__email"]
