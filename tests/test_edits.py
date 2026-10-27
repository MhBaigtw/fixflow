"""Architect review fixes: priority ownership, audited edits, login case, unit race."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.maintenance.models import MaintenanceRequest, RequestEvent
from apps.properties.models import Unit

from .conftest import assert_error
from .factories import PASSWORD, TenantFactory

pytestmark = pytest.mark.django_db
S = MaintenanceRequest.Status
EDIT = RequestEvent.EventType.EDIT


def patch(client, req, data):
    return client.patch(f"/api/requests/{req.id}/", data, format="json")


def make_routine(wf, status=S.SUBMITTED):
    created = timezone.now() - timedelta(hours=5)
    return wf.make(
        status,
        priority="routine",
        requested_priority="routine",
        created_at=created,
        sla_due_at=created + timedelta(days=7),
    )


# --- A1: case-insensitive login -------------------------------------------------


@pytest.mark.parametrize(
    "typed", ["Alice@Example.COM", "  alice@example.com  ", "ALICE@EXAMPLE.COM"]
)
def test_login_email_is_case_insensitive(anon, typed):
    TenantFactory(email="alice@example.com")
    resp = anon.post("/api/auth/token/", {"email": typed, "password": PASSWORD})
    assert resp.status_code == 200, resp.content
    assert {"access", "refresh"} <= set(resp.json())


def test_mixed_case_login_with_wrong_password_is_401(anon):
    TenantFactory(email="alice@example.com")
    resp = anon.post("/api/auth/token/", {"email": "Alice@Example.com", "password": "nope"})
    assert_error(resp, 401, "no_active_account")


# --- A2: requested vs working priority -----------------------------------------


def test_create_sets_requested_and_working_priority(wf, client_for):
    resp = client_for(wf["creator"]).post(
        "/api/requests/",
        {
            "unit": wf["unit"].id,
            "title": "t",
            "description": "d",
            "category": "other",
            "priority": "urgent",
        },
        format="json",
    )
    body = resp.json()
    assert (body["requested_priority"], body["priority"]) == ("urgent", "urgent")


def test_tenant_priority_edit_updates_both_and_recomputes_sla(wf, client_for):
    req = make_routine(wf)
    resp = patch(client_for(wf["creator"]), req, {"priority": "emergency"})
    assert resp.status_code == 200
    req.refresh_from_db()
    assert (req.requested_priority, req.priority) == ("emergency", "emergency")
    assert req.sla_due_at == req.created_at + timedelta(hours=24)


@pytest.mark.parametrize("status", [S.SUBMITTED, S.TRIAGED, S.ASSIGNED, S.IN_PROGRESS])
def test_manager_can_change_priority_until_resolved(wf, client_for, status):
    req = make_routine(wf, status)
    resp = patch(client_for(wf["manager"]), req, {"priority": "urgent"})
    assert resp.status_code == 200, resp.content
    req.refresh_from_db()
    assert req.priority == "urgent"
    assert req.requested_priority == "routine"  # what the tenant asked for is kept
    assert req.sla_due_at == req.created_at + timedelta(hours=72)
    assert req.status == status


@pytest.mark.parametrize("status", [S.RESOLVED, S.CLOSED, S.CANCELLED])
def test_manager_cannot_change_priority_after_work_is_done(wf, client_for, status):
    req = make_routine(wf, status)
    resp = patch(client_for(wf["manager"]), req, {"priority": "urgent"})
    error = assert_error(resp, 409, "priority_not_editable")
    assert error["details"] == {"current_status": status}
    req.refresh_from_db()
    assert req.priority == "routine"
    assert not req.events.exists()


@pytest.mark.parametrize(
    "data",
    [
        {"title": "x"},
        {"description": "x"},
        {"category": "pest"},
        {"priority": "urgent", "title": "x"},
    ],
)
def test_manager_can_patch_priority_only(wf, client_for, data):
    req = make_routine(wf)
    resp = patch(client_for(wf["manager"]), req, data)
    error = assert_error(resp, 400, "validation_error")
    assert set(error["details"]) == set(data) - {"priority"}
    req.refresh_from_db()
    assert (req.title, req.priority) != ("x", "urgent")
    assert not req.events.exists()


def test_other_manager_gets_404(wf, client_for):
    req = make_routine(wf)
    assert_error(patch(client_for(wf["other_manager"]), req, {"priority": "urgent"}), 404)


def test_tenant_cannot_patch_after_submitted(wf, client_for):
    req = make_routine(wf, S.TRIAGED)
    resp = patch(client_for(wf["creator"]), req, {"priority": "emergency"})
    assert_error(resp, 409, "request_not_editable")


def test_triage_can_set_priority(wf, client_for):
    req = make_routine(wf)
    resp = client_for(wf["manager"]).post(
        f"/api/requests/{req.id}/transition/",
        {"action": "triage", "priority": "emergency"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    req.refresh_from_db()
    assert (req.status, req.priority, req.requested_priority) == (S.TRIAGED, "emergency", "routine")
    assert req.sla_due_at == req.created_at + timedelta(hours=24)

    event = req.events.get()
    assert event.event_type == RequestEvent.EventType.STATUS_CHANGE
    assert (event.from_status, event.to_status) == (S.SUBMITTED, S.TRIAGED)
    assert event.changes["priority"] == {"from": "routine", "to": "emergency"}
    assert set(event.changes) == {"priority", "sla_due_at"}


def test_triage_with_same_priority_records_no_changes(wf, client_for):
    req = make_routine(wf)
    client_for(wf["manager"]).post(
        f"/api/requests/{req.id}/transition/",
        {"action": "triage", "priority": "routine"},
        format="json",
    )
    assert req.events.get().changes == {}


@pytest.mark.parametrize("action,status", [("assign", S.TRIAGED), ("cancel", S.SUBMITTED)])
def test_priority_only_allowed_with_triage(wf, client_for, action, status):
    req = make_routine(wf, status)
    resp = client_for(wf["manager"]).post(
        f"/api/requests/{req.id}/transition/",
        {"action": action, "priority": "urgent", "contractor_id": wf["contractor"].id},
        format="json",
    )
    assert "priority" in assert_error(resp, 400, "validation_error")["details"]
    req.refresh_from_db()
    assert req.status == status


# --- A3: audited edits ---------------------------------------------------------


def test_tenant_edit_writes_one_edit_event_with_diff(wf, client_for):
    req = make_routine(wf)
    old_due, old_title = req.sla_due_at, req.title
    resp = patch(
        client_for(wf["creator"]),
        req,
        {"title": "New", "category": "electrical", "priority": "urgent"},
    )
    assert resp.status_code == 200
    req.refresh_from_db()

    event = req.events.get()
    assert event.event_type == EDIT
    assert event.actor == wf["creator"]
    assert (event.from_status, event.to_status) == (S.SUBMITTED, S.SUBMITTED)
    assert event.changes == {
        "title": {"from": old_title, "to": "New"},
        "category": {"from": "plumbing", "to": "electrical"},
        "requested_priority": {"from": "routine", "to": "urgent"},
        "priority": {"from": "routine", "to": "urgent"},
        "sla_due_at": {"from": old_due.isoformat(), "to": req.sla_due_at.isoformat()},
    }


def test_manager_priority_edit_audit(wf, client_for):
    req = make_routine(wf, S.ASSIGNED)
    old_due = req.sla_due_at
    patch(client_for(wf["manager"]), req, {"priority": "urgent"})
    req.refresh_from_db()
    event = req.events.get()
    assert event.event_type == EDIT
    assert event.actor == wf["manager"]
    assert (event.from_status, event.to_status) == (S.ASSIGNED, S.ASSIGNED)
    assert event.changes == {
        "priority": {"from": "routine", "to": "urgent"},
        "sla_due_at": {"from": old_due.isoformat(), "to": req.sla_due_at.isoformat()},
    }


@pytest.mark.parametrize(
    "actor,data",
    [
        ("creator", {"category": "plumbing"}),
        ("creator", {"priority": "routine", "title": None}),
        ("manager", {"priority": "routine"}),
    ],
)
def test_noop_patch_writes_no_event(wf, client_for, actor, data):
    req = make_routine(wf)
    if "title" in data:
        data["title"] = req.title
    updated_at = req.updated_at
    resp = patch(client_for(wf[actor]), req, data)
    assert resp.status_code == 200
    req.refresh_from_db()
    assert not req.events.exists()
    assert req.updated_at == updated_at


def test_edit_events_exposed_in_api(wf, client_for):
    req = make_routine(wf)
    patch(client_for(wf["creator"]), req, {"title": "Renamed"})
    results = client_for(wf["roommate"]).get(f"/api/requests/{req.id}/events/").json()["results"]
    assert len(results) == 1
    assert results[0]["event_type"] == "edit"
    assert results[0]["changes"]["title"]["to"] == "Renamed"


def test_edit_events_stay_append_only(wf, client_for):
    from apps.maintenance.models import AppendOnlyError

    req = make_routine(wf)
    patch(client_for(wf["creator"]), req, {"title": "Renamed"})
    event = req.events.get()
    event.changes = {}
    with pytest.raises(AppendOnlyError):
        event.save()


# --- A4: unit label race ---------------------------------------------------------


def test_unit_label_race_maps_integrity_error_to_400(wf, client_for, monkeypatch):
    """Simulate a concurrent create winning between the serializer's check and the insert."""
    real_filter = Unit.objects.filter

    class NoExists:
        def __init__(self, qs):
            self.qs = qs

        def exists(self):
            return False

    monkeypatch.setattr(Unit.objects, "filter", lambda *a, **kw: NoExists(real_filter(*a, **kw)))
    resp = client_for(wf["manager"]).post(
        f"/api/properties/{wf['property'].id}/units/", {"label": wf["unit"].label}
    )
    assert "label" in assert_error(resp, 400, "validation_error")["details"]
