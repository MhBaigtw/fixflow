from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.exceptions import Conflict
from apps.maintenance.models import MaintenanceRequest, RequestEvent
from apps.properties.selectors import active_tenancies

from .audit import record_change, record_event
from .priority import compute_sla_due_at, set_priority
from .transitions import is_property_manager

S = MaintenanceRequest.Status
EDITABLE_FIELDS = ("title", "description", "category", "priority")
# The manager may re-prioritise until the work is done.
MANAGER_PRIORITY_STATUSES = (S.SUBMITTED, S.TRIAGED, S.ASSIGNED, S.IN_PROGRESS)


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
            requested_priority=priority,
            priority=priority,
            status=S.SUBMITTED,
            created_at=now,
            sla_due_at=compute_sla_due_at(now, priority),
        )
        record_event(req, actor=tenant, from_status=None)
    return req


def update_request(*, request_id, user, **fields):
    """PATCH rules.

    - The creating tenant may edit title/description/category/priority while
      submitted. Their priority is both the requested and the working priority.
    - The property manager may change only `priority`, while submitted, triaged,
      assigned or in_progress.
    - Anyone else who can see the request gets 403.

    Any real change writes exactly one `edit` event with a field-level diff; a PATCH
    that changes nothing writes nothing.
    """
    with transaction.atomic():
        # Locked so an edit can't interleave with a concurrent transition.
        req = MaintenanceRequest.objects.select_for_update().get(pk=request_id)
        changes = {}
        if req.created_by_id == user.id:
            if req.status != S.SUBMITTED:
                raise Conflict(
                    "Requests can only be edited while submitted.",
                    code="request_not_editable",
                    details={"current_status": req.status},
                )
            for field in ("title", "description", "category"):
                if field in fields:
                    record_change(changes, field, getattr(req, field), fields[field])
                    setattr(req, field, fields[field])
            if "priority" in fields:
                record_change(
                    changes, "requested_priority", req.requested_priority, fields["priority"]
                )
                req.requested_priority = fields["priority"]
                set_priority(req, fields["priority"], changes)
        elif is_property_manager(user, req):
            others = sorted(set(fields) - {"priority"})
            if others:
                raise ValidationError({f: ["Managers can only change priority."] for f in others})
            if req.status not in MANAGER_PRIORITY_STATUSES:
                raise Conflict(
                    "Priority can only be changed before the request is resolved.",
                    code="priority_not_editable",
                    details={"current_status": req.status},
                )
            set_priority(req, fields["priority"], changes)
        else:
            raise PermissionDenied(
                "Only the tenant who created this request or its property manager can edit it."
            )

        if changes:
            req.save()
            record_event(
                req,
                actor=user,
                from_status=req.status,
                event_type=RequestEvent.EventType.EDIT,
                changes=changes,
            )
    return req
