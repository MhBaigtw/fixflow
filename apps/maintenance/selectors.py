"""Read-side scoping for maintenance requests.

Phase 3: requests come with their unit joined (select_related) because the
serializer's `property` field reads unit.property_id. Before this, every list row
cost one extra query (see docs/benchmarks.md, Baseline vs Optimized).
"""

from django.db.models import (
    Aggregate,
    Case,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    Value,
    When,
)
from django.db.models.functions import Now

from apps.properties.selectors import active_tenancies

from .models import MaintenanceRequest

P = MaintenanceRequest.Priority
S = MaintenanceRequest.Status


def requests_visible_to(user):
    """tenant: requests on units they actively rent (roommates share them);
    manager: requests on properties they manage; contractor: requests assigned to them."""
    qs = MaintenanceRequest.objects.select_related("unit")
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


class Median(Aggregate):
    """Postgres percentile_cont(0.5) WITHIN GROUP (ORDER BY <expr>): the continuous
    median (the mean of the two middle values when the count is even)."""

    function = "percentile_cont"
    template = "%(function)s(0.5) WITHIN GROUP (ORDER BY %(expressions)s)"


OPEN_STATUSES = (S.SUBMITTED, S.TRIAGED, S.ASSIGNED, S.IN_PROGRESS)


def manager_stats(user, *, property_id=None, created_after=None, created_before=None):
    """Stats over the requests on properties `user` manages, optionally narrowed to one
    property and/or a created_at window (inclusive bounds).

    - open_by_status: count per open status (submitted, triaged, assigned, in_progress).
    - overdue: requests whose SLA clock is running and sla_due_at < now (DB time).
    - median_hours_to_resolve: per category, the median of (resolved_at - created_at)
      in hours over requests currently resolved or closed. For a request that was
      reopened and resolved again, resolved_at is the final resolution, so the
      duration includes the reopen period. null when a category has no such requests.
    """
    qs = MaintenanceRequest.objects.filter(unit__property__manager=user)
    if property_id is not None:
        qs = qs.filter(unit__property_id=property_id)
    if created_after is not None:
        qs = qs.filter(created_at__gte=created_after)
    if created_before is not None:
        qs = qs.filter(created_at__lte=created_before)

    open_counts = dict.fromkeys(OPEN_STATUSES, 0)
    for row in qs.filter(status__in=OPEN_STATUSES).values("status").annotate(n=Count("id")):
        open_counts[row["status"]] = row["n"]

    medians = dict.fromkeys(MaintenanceRequest.Category.values)
    resolved = qs.filter(status__in=(S.RESOLVED, S.CLOSED), resolved_at__isnull=False)
    duration = ExpressionWrapper(F("resolved_at") - F("created_at"), output_field=DurationField())
    for row in (
        resolved.values("category")
        .annotate(median=Median(duration, output_field=DurationField()))
        .order_by("category")
    ):
        medians[row["category"]] = round(row["median"].total_seconds() / 3600, 2)

    return {
        "open_by_status": open_counts,
        "overdue": qs.filter(overdue_q()).count(),
        "median_hours_to_resolve": medians,
    }


def comments_visible_to(user, req):
    qs = req.comments.all()
    if user.is_tenant:
        qs = qs.filter(is_internal=False)
    return qs
