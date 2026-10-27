from datetime import datetime

from apps.maintenance.models import RequestEvent


def _jsonable(value):
    return value.isoformat() if isinstance(value, datetime) else value


def record_change(changes, field, old, new):
    """Add {"field": {"from": old, "to": new}} to `changes` if the value moved."""
    if old != new:
        changes[field] = {"from": _jsonable(old), "to": _jsonable(new)}


def record_event(
    req,
    *,
    actor,
    from_status,
    note="",
    event_type=RequestEvent.EventType.STATUS_CHANGE,
    changes=None,
):
    return RequestEvent.objects.create(
        request=req,
        actor=actor,
        event_type=event_type,
        from_status=from_status,
        to_status=req.status,
        note=note,
        changes=changes or {},
    )
