from django.conf import settings
from django.db import models
from django.utils import timezone


class MaintenanceRequest(models.Model):
    class Category(models.TextChoices):
        PLUMBING = "plumbing", "Plumbing"
        ELECTRICAL = "electrical", "Electrical"
        HEATING = "heating", "Heating"
        APPLIANCE = "appliance", "Appliance"
        PEST = "pest", "Pest"
        STRUCTURAL = "structural", "Structural"
        OTHER = "other", "Other"

    class Priority(models.TextChoices):
        EMERGENCY = "emergency", "Emergency"
        URGENT = "urgent", "Urgent"
        ROUTINE = "routine", "Routine"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        TRIAGED = "triaged", "Triaged"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    # The SLA clock stops in these statuses; a request in them is never overdue.
    SLA_STOPPED_STATUSES = (Status.RESOLVED, Status.CLOSED, Status.CANCELLED)

    unit = models.ForeignKey(
        "properties.Unit", on_delete=models.PROTECT, related_name="maintenance_requests"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_requests"
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices)
    # What the tenant asked for. `priority` is the working priority the manager can
    # override; it drives the SLA.
    requested_priority = models.CharField(max_length=20, choices=Priority.choices)
    priority = models.CharField(max_length=20, choices=Priority.choices)
    # Changed only by apps.maintenance.services.transitions.
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_requests",
        limit_choices_to={"role": "contractor"},
    )
    sla_due_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        # Each index was kept only after EXPLAIN (ANALYZE) on the 10k dataset showed it
        # turned a full scan + sort into an ordered index scan for a real list query.
        # Evidence: docs/benchmarks.md, "Index decisions".
        indexes = [
            # Default ordering for manager lists (and the overdue filter's page).
            models.Index(fields=["-created_at", "-id"], name="req_created_idx"),
            # Contractor lists: filter by assignee, already in default order.
            models.Index(
                fields=["assigned_to", "-created_at", "-id"], name="req_assignee_created_idx"
            ),
            # ?ordering=sla_due_at ("what's due soonest").
            models.Index(fields=["sla_due_at"], name="req_sla_due_idx"),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title} [{self.status}]"

    @property
    def is_overdue(self):
        return self.status not in self.SLA_STOPPED_STATUSES and timezone.now() > self.sla_due_at


class AppendOnlyError(RuntimeError):
    pass


class RequestEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("RequestEvent rows are append-only.")

    def delete(self):
        raise AppendOnlyError("RequestEvent rows are append-only.")


class RequestEvent(models.Model):
    """Append-only audit log of status changes and edits.

    status_change: from_status -> to_status, plus any field changes made with it.
    edit: from_status == to_status == the current status; `changes` holds
    {"field": {"from": old, "to": new}, ...}.
    """

    class EventType(models.TextChoices):
        STATUS_CHANGE = "status_change", "Status change"
        EDIT = "edit", "Edit"

    request = models.ForeignKey(MaintenanceRequest, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(
        max_length=20, choices=EventType.choices, default=EventType.STATUS_CHANGE
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="request_events"
    )
    # NULL (not "") on the creation event: there was no previous status.
    from_status = models.CharField(  # noqa: DJ001
        max_length=20, choices=MaintenanceRequest.Status.choices, null=True, blank=True
    )
    to_status = models.CharField(max_length=20, choices=MaintenanceRequest.Status.choices)
    note = models.TextField(blank=True, default="")
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = RequestEventQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"#{self.request_id}: {self.from_status} -> {self.to_status}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("RequestEvent rows are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("RequestEvent rows are append-only.")


class Comment(models.Model):
    request = models.ForeignKey(
        MaintenanceRequest, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="request_comments"
    )
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Comment #{self.pk} on #{self.request_id}"
