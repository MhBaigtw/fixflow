from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.exceptions import Conflict
from apps.maintenance.models import MaintenanceRequest
from apps.properties.selectors import active_tenancies

from .transitions import record_event

EDITABLE_FIELDS = ("title", "description", "category", "priority")


def compute_sla_due_at(created_at, priority):
    return created_at + timedelta(hours=settings.MAINTENANCE_SLA_HOURS[priority])


def create_request(*, tenant, unit, title, description, category, priority):
    if not active_tenancies(tenant).filter(unit=unit).exists():
        raise ValidationError({"unit": ["You do not have an active tenancy on this unit."]})
    with transaction.atomic():
        now = timezone.now()
        req = MaintenanceRequest.objects.create(
            unit=unit,
            created_by=tenant,
            title=title,
            description=description,
            category=category,
            priority=priority,
            status=MaintenanceRequest.Status.SUBMITTED,
            created_at=now,
            sla_due_at=compute_sla_due_at(now, priority),
        )
        record_event(req, actor=tenant, from_status=None)
    return req


def update_request(*, request_id, user, **changes):
    """Edit by the creating tenant while the request is still submitted."""
    with transaction.atomic():
        # Locked so an edit can't interleave with a concurrent triage.
        req = MaintenanceRequest.objects.select_for_update().get(pk=request_id)
        if req.created_by_id != user.id:
            raise PermissionDenied("Only the tenant who created this request can edit it.")
        if req.status != MaintenanceRequest.Status.SUBMITTED:
            raise Conflict(
                "Requests can only be edited while submitted.",
                code="request_not_editable",
                details={"current_status": req.status},
            )
        for field, value in changes.items():
            setattr(req, field, value)
        if "priority" in changes:
            req.sla_due_at = compute_sla_due_at(req.created_at, req.priority)
        req.save()
    return req
