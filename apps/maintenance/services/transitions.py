"""The workflow state machine. This module is the ONLY place a request's status changes.

TRANSITIONS is the single table of legal moves. apply_transition() locks the row,
re-checks the status after locking, applies the move and writes exactly one
RequestEvent, all in one transaction.

Check order: unknown action -> 400; move not legal from the current status -> 409
invalid_transition; legal move, wrong actor -> 403. (Actors who cannot see the
request at all never get here: the view's scoped lookup returns 404 first.)
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.core.exceptions import Conflict, InvalidTransition
from apps.maintenance.models import MaintenanceRequest, RequestEvent
from apps.properties.models import Tenancy

S = MaintenanceRequest.Status


def is_property_manager(user, req):
    return req.unit.property.manager_id == user.id


def is_assigned_contractor(user, req):
    return req.assigned_to_id is not None and req.assigned_to_id == user.id


def is_current_tenant(user, req):
    return (
        user.is_tenant
        and Tenancy.objects.filter(unit_id=req.unit_id, tenant=user, end_date__isnull=True).exists()
    )


def is_current_tenant_or_manager(user, req):
    return is_current_tenant(user, req) or is_property_manager(user, req)


def is_creator_or_manager(user, req):
    return (req.created_by_id == user.id and is_current_tenant(user, req)) or is_property_manager(
        user, req
    )


@dataclass(frozen=True)
class Transition:
    action: str
    from_status: str
    to_status: str
    allowed: Callable[[User, MaintenanceRequest], bool]


TRANSITIONS = [
    Transition("triage", S.SUBMITTED, S.TRIAGED, is_property_manager),
    Transition("assign", S.TRIAGED, S.ASSIGNED, is_property_manager),
    Transition("start", S.ASSIGNED, S.IN_PROGRESS, is_assigned_contractor),
    Transition("resolve", S.IN_PROGRESS, S.RESOLVED, is_assigned_contractor),
    Transition("close", S.RESOLVED, S.CLOSED, is_current_tenant_or_manager),
    Transition("reopen", S.RESOLVED, S.TRIAGED, is_current_tenant),
    Transition("cancel", S.SUBMITTED, S.CANCELLED, is_creator_or_manager),
    Transition("cancel", S.TRIAGED, S.CANCELLED, is_property_manager),
    Transition("cancel", S.ASSIGNED, S.CANCELLED, is_property_manager),
]

_BY_ACTION_AND_STATUS = {(t.action, t.from_status): t for t in TRANSITIONS}
ACTIONS = sorted({t.action for t in TRANSITIONS})


def find_transition(action, from_status):
    return _BY_ACTION_AND_STATUS.get((action, from_status))


def apply_transition(*, request_id, actor, action, note="", contractor_id=None):
    if action not in ACTIONS:
        raise ValidationError({"action": [f"Unknown action. Choose one of: {', '.join(ACTIONS)}."]})

    with transaction.atomic():
        # Lock first, then read the status: a concurrent transition that committed
        # while we waited is visible here, so two racing moves can't both succeed.
        req = MaintenanceRequest.objects.select_for_update().get(pk=request_id)
        transition = find_transition(action, req.status)
        if transition is None:
            raise InvalidTransition(
                f"Cannot '{action}' a request whose status is '{req.status}'.",
                details={"action": action, "current_status": req.status},
            )
        if not transition.allowed(actor, req):
            raise PermissionDenied("You are not allowed to perform this transition.")

        now = timezone.now()
        if action == "assign":
            req.assigned_to = _get_contractor(contractor_id)
        elif action == "resolve":
            req.resolved_at = now
        elif action == "close":
            req.closed_at = now
        elif action == "reopen":
            window = timedelta(days=settings.MAINTENANCE_REOPEN_WINDOW_DAYS)
            if now - req.resolved_at > window:
                raise Conflict(
                    f"Requests can only be reopened within "
                    f"{settings.MAINTENANCE_REOPEN_WINDOW_DAYS} days of being resolved.",
                    code="reopen_window_expired",
                    details={"resolved_at": req.resolved_at.isoformat()},
                )
            req.resolved_at = None
            req.assigned_to = None

        from_status = req.status
        req.status = transition.to_status
        req.save()
        record_event(req, actor=actor, from_status=from_status, note=note)
    return req


def record_event(req, *, actor, from_status, note=""):
    return RequestEvent.objects.create(
        request=req, actor=actor, from_status=from_status, to_status=req.status, note=note
    )


def _get_contractor(contractor_id):
    if contractor_id is None:
        raise ValidationError({"contractor_id": ["This field is required for 'assign'."]})
    contractor = User.objects.filter(
        pk=contractor_id, role=User.Role.CONTRACTOR, is_active=True
    ).first()
    if contractor is None:
        raise ValidationError({"contractor_id": ["No active contractor with this id."]})
    return contractor
