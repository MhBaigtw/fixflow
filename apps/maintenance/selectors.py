"""Read-side scoping for maintenance requests.

Phase 2 keeps these querysets deliberately plain (no select_related /
prefetch_related): Phase 3 benchmarks this baseline before optimizing.
"""

from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Now

from apps.properties.selectors import active_tenancies

from .models import MaintenanceRequest

P = MaintenanceRequest.Priority


def requests_visible_to(user):
    """tenant: requests on units they actively rent (roommates share them);
    manager: requests on properties they manage; contractor: requests assigned to them."""
    qs = MaintenanceRequest.objects.all()
    if user.is_manager:
        return qs.filter(unit__property__manager=user)
    if user.is_contractor:
        return qs.filter(assigned_to=user)
    if user.is_tenant:
        return qs.filter(unit_id__in=active_tenancies(user).values("unit_id"))
    return qs.none()


def with_priority_rank(qs):
    """Rank for ordering by urgency (emergency first), not alphabetically."""
    return qs.annotate(
        priority_rank=Case(
            When(priority=P.EMERGENCY, then=Value(0)),
            When(priority=P.URGENT, then=Value(1)),
            When(priority=P.ROUTINE, then=Value(2)),
            output_field=IntegerField(),
        )
    )


def overdue_q():
    """Overdue = SLA clock still running and the deadline has passed (DB time)."""
    return Q(sla_due_at__lt=Now()) & ~Q(status__in=MaintenanceRequest.SLA_STOPPED_STATUSES)


def comments_visible_to(user, req):
    qs = req.comments.all()
    if user.is_tenant:
        qs = qs.filter(is_internal=False)
    return qs
