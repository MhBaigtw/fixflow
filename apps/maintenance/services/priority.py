from datetime import timedelta

from django.conf import settings

from .audit import record_change


def compute_sla_due_at(created_at, priority):
    return created_at + timedelta(hours=settings.MAINTENANCE_SLA_HOURS[priority])


def set_priority(req, priority, changes):
    """Set the working priority and recompute the SLA from created_at.

    Records both changes into `changes` (for the audit event). No-op if unchanged.
    """
    if priority == req.priority:
        return
    old_due = req.sla_due_at
    record_change(changes, "priority", req.priority, priority)
    req.priority = priority
    req.sla_due_at = compute_sla_due_at(req.created_at, priority)
    record_change(changes, "sla_due_at", old_due, req.sla_due_at)
