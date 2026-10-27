"""Admin is read-mostly: status and workflow fields change only via the transition
service, and events are append-only, so neither is editable here."""

from django.contrib import admin

from .models import Comment, MaintenanceRequest, RequestEvent


class RequestEventInline(admin.TabularInline):
    model = RequestEvent
    fields = ["created_at", "actor", "from_status", "to_status", "note"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "unit", "status", "priority", "assigned_to", "sla_due_at"]
    list_filter = ["status", "priority", "category"]
    search_fields = ["title", "description"]
    readonly_fields = [
        "unit",
        "created_by",
        "status",
        "assigned_to",
        "sla_due_at",
        "resolved_at",
        "closed_at",
        "created_at",
        "updated_at",
    ]
    inlines = [RequestEventInline]

    def has_add_permission(self, request):
        return False  # creation must go through the service (writes the initial event)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RequestEvent)
class RequestEventAdmin(admin.ModelAdmin):
    list_display = ["request", "from_status", "to_status", "actor", "created_at"]
    list_filter = ["to_status"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ["request", "author", "is_internal", "created_at"]
    list_filter = ["is_internal"]
