"""The full transition matrix, the reopen window, and the audit trail."""

from datetime import timedelta
from itertools import product

import pytest
from django.utils import timezone

from apps.maintenance.models import MaintenanceRequest, RequestEvent
from apps.maintenance.services.transitions import ACTIONS, TRANSITIONS

from .conftest import assert_error

pytestmark = pytest.mark.django_db
S = MaintenanceRequest.Status

# Written out independently of the code's table, straight from the spec.
EXPECTED_LEGAL = {
    ("triage", S.SUBMITTED): S.TRIAGED,
    ("assign", S.TRIAGED): S.ASSIGNED,
    ("start", S.ASSIGNED): S.IN_PROGRESS,
    ("resolve", S.IN_PROGRESS): S.RESOLVED,
    ("close", S.RESOLVED): S.CLOSED,
    ("reopen", S.RESOLVED): S.TRIAGED,
    ("cancel", S.SUBMITTED): S.CANCELLED,
    ("cancel", S.TRIAGED): S.CANCELLED,
    ("cancel", S.ASSIGNED): S.CANCELLED,
}

ILLEGAL = sorted(
    (action, status)
    for action, status in product(ACTIONS, S.values)
    if (action, status) not in EXPECTED_LEGAL
)

# (action, from_status, actor): every actor the spec allows for each legal move.
RIGHT_ACTORS = [
    ("triage", S.SUBMITTED, "manager"),
    ("assign", S.TRIAGED, "manager"),
    ("start", S.ASSIGNED, "contractor"),
    ("resolve", S.IN_PROGRESS, "contractor"),
    ("close", S.RESOLVED, "creator"),
    ("close", S.RESOLVED, "roommate"),
    ("close", S.RESOLVED, "manager"),
    ("reopen", S.RESOLVED, "creator"),
    ("reopen", S.RESOLVED, "roommate"),
    ("cancel", S.SUBMITTED, "creator"),
    ("cancel", S.SUBMITTED, "manager"),
    ("cancel", S.TRIAGED, "manager"),
    ("cancel", S.ASSIGNED, "manager"),
]

# (action, from_status, actor, expected): 403 if the actor can see the request, else 404.
WRONG_ACTORS = [
    ("triage", S.SUBMITTED, "creator", 403),
    ("triage", S.SUBMITTED, "roommate", 403),
    ("triage", S.SUBMITTED, "other_manager", 404),
    ("triage", S.SUBMITTED, "contractor", 404),
    ("triage", S.SUBMITTED, "outsider", 404),
    ("assign", S.TRIAGED, "creator", 403),
    ("assign", S.TRIAGED, "other_manager", 404),
    ("assign", S.TRIAGED, "contractor", 404),
    ("start", S.ASSIGNED, "manager", 403),
    ("start", S.ASSIGNED, "creator", 403),
    ("start", S.ASSIGNED, "other_contractor", 404),
    ("resolve", S.IN_PROGRESS, "manager", 403),
    ("resolve", S.IN_PROGRESS, "roommate", 403),
    ("resolve", S.IN_PROGRESS, "other_contractor", 404),
    ("close", S.RESOLVED, "contractor", 403),
    ("close", S.RESOLVED, "other_manager", 404),
    ("close", S.RESOLVED, "outsider", 404),
    ("reopen", S.RESOLVED, "manager", 403),
    ("reopen", S.RESOLVED, "contractor", 403),
    ("reopen", S.RESOLVED, "outsider", 404),
    ("cancel", S.SUBMITTED, "roommate", 403),
    ("cancel", S.SUBMITTED, "other_manager", 404),
    ("cancel", S.SUBMITTED, "outsider", 404),
    ("cancel", S.TRIAGED, "creator", 403),
    ("cancel", S.TRIAGED, "roommate", 403),
    ("cancel", S.ASSIGNED, "creator", 403),
    ("cancel", S.ASSIGNED, "contractor", 403),
]


def post_transition(client, req, action, **extra):
    return client.post(
        f"/api/requests/{req.id}/transition/", {"action": action, **extra}, format="json"
    )


def test_table_matches_spec():
    assert {(t.action, t.from_status): t.to_status for t in TRANSITIONS} == EXPECTED_LEGAL
    # 7 statuses x 7 actions = 49 pairs; 9 legal, 40 illegal.
    assert len(ILLEGAL) == 40


@pytest.mark.parametrize("action,from_status,actor", RIGHT_ACTORS)
def test_legal_transition_by_right_actor(wf, client_for, action, from_status, actor):
    req = wf.make(from_status)
    extra = {"contractor_id": wf["other_contractor"].id} if action == "assign" else {}
    resp = post_transition(client_for(wf[actor]), req, action, note="ok", **extra)

    assert resp.status_code == 200, resp.content
    expected_to = EXPECTED_LEGAL[(action, from_status)]
    assert resp.json()["status"] == expected_to
    req.refresh_from_db()
    assert req.status == expected_to

    events = list(RequestEvent.objects.filter(request=req))
    assert len(events) == 1
    assert (events[0].from_status, events[0].to_status) == (from_status, expected_to)
    assert events[0].actor == wf[actor]
    assert events[0].note == "ok"


@pytest.mark.parametrize("action,from_status", ILLEGAL)
def test_illegal_transition_returns_409(wf, client_for, action, from_status):
    req = wf.make(from_status)
    # The manager can see every request on the property, so 409 isn't masked by 404.
    resp = post_transition(
        client_for(wf["manager"]), req, action, contractor_id=wf["contractor"].id
    )
    error = assert_error(resp, 409, "invalid_transition")
    assert error["details"] == {"action": action, "current_status": from_status}
    req.refresh_from_db()
    assert req.status == from_status
    assert not RequestEvent.objects.filter(request=req).exists()


@pytest.mark.parametrize("action,from_status,actor,expected", WRONG_ACTORS)
def test_legal_transition_by_wrong_actor(wf, client_for, action, from_status, actor, expected):
    req = wf.make(from_status)
    extra = {"contractor_id": wf["contractor"].id} if action == "assign" else {}
    resp = post_transition(client_for(wf[actor]), req, action, **extra)
    assert_error(resp, expected, "permission_denied" if expected == 403 else "not_found")
    req.refresh_from_db()
    assert req.status == from_status
    assert not RequestEvent.objects.filter(request=req).exists()


def test_creator_whose_tenancy_ended_cannot_cancel(wf, client_for):
    req = wf.make(S.SUBMITTED)
    wf["creator"].tenancies.update(end_date=timezone.localdate())
    assert_error(post_transition(client_for(wf["creator"]), req, "cancel"), 404)


def test_side_effects(wf, client_for):
    manager = client_for(wf["manager"])
    contractor = client_for(wf["contractor"])
    req = wf.make(S.TRIAGED)

    post_transition(manager, req, "assign", contractor_id=wf["contractor"].id)
    req.refresh_from_db()
    assert req.assigned_to == wf["contractor"]

    post_transition(contractor, req, "start")
    post_transition(contractor, req, "resolve")
    req.refresh_from_db()
    assert req.resolved_at is not None and req.closed_at is None

    post_transition(client_for(wf["creator"]), req, "close")
    req.refresh_from_db()
    assert req.status == S.CLOSED and req.closed_at is not None


@pytest.mark.parametrize(
    "contractor_id_for", [None, "creator", "manager", "missing", "inactive_contractor"]
)
def test_assign_requires_active_contractor(wf, client_for, contractor_id_for):
    req = wf.make(S.TRIAGED)
    if contractor_id_for is None:
        extra = {}
    elif contractor_id_for == "missing":
        extra = {"contractor_id": 999999}
    elif contractor_id_for == "inactive_contractor":
        wf["other_contractor"].is_active = False
        wf["other_contractor"].save()
        extra = {"contractor_id": wf["other_contractor"].id}
    else:
        extra = {"contractor_id": wf[contractor_id_for].id}
    resp = post_transition(client_for(wf["manager"]), req, "assign", **extra)
    assert "contractor_id" in assert_error(resp, 400, "validation_error")["details"]
    req.refresh_from_db()
    assert req.status == S.TRIAGED


def test_unknown_action_is_400(wf, client_for):
    req = wf.make()
    resp = post_transition(client_for(wf["manager"]), req, "teleport")
    assert "action" in assert_error(resp, 400, "validation_error")["details"]


def test_unknown_action_rejected_by_service(wf):
    from rest_framework.exceptions import ValidationError

    from apps.maintenance.services.transitions import apply_transition

    req = wf.make()
    with pytest.raises(ValidationError):
        apply_transition(request_id=req.id, actor=wf["manager"], action="teleport")


# --- Reopen window -------------------------------------------------------------


@pytest.mark.parametrize("days_ago,ok", [(0, True), (13, True), (13.99, True), (14.01, False)])
def test_reopen_window(wf, client_for, days_ago, ok):
    req = wf.make(S.RESOLVED, resolved_at=timezone.now() - timedelta(days=days_ago))
    resp = post_transition(client_for(wf["creator"]), req, "reopen")
    req.refresh_from_db()
    if ok:
        assert resp.status_code == 200
        assert req.status == S.TRIAGED
        assert req.resolved_at is None
        assert req.assigned_to is None
    else:
        assert_error(resp, 409, "reopen_window_expired")
        assert req.status == S.RESOLVED
        assert not req.events.exists()


def test_reopen_window_is_configurable(wf, client_for, settings):
    settings.MAINTENANCE_REOPEN_WINDOW_DAYS = 3
    req = wf.make(S.RESOLVED, resolved_at=timezone.now() - timedelta(days=4))
    assert_error(post_transition(client_for(wf["creator"]), req, "reopen"), 409)


def test_reopened_request_drops_out_of_contractor_view(wf, client_for):
    req = wf.make(S.RESOLVED)
    post_transition(client_for(wf["creator"]), req, "reopen")
    assert_error(client_for(wf["contractor"]).get(f"/api/requests/{req.id}/"), 404)


# --- Audit ---------------------------------------------------------------------


def test_full_lifecycle_audit_trail(wf, client_for):
    tenant = client_for(wf["creator"])
    manager = client_for(wf["manager"])
    contractor = client_for(wf["contractor"])

    resp = tenant.post(
        "/api/requests/",
        {
            "unit": wf["unit"].id,
            "title": "Leaking tap",
            "description": "Kitchen tap drips.",
            "category": "plumbing",
            "priority": "routine",
        },
        format="json",
    )
    assert resp.status_code == 201
    req = MaintenanceRequest.objects.get(pk=resp.json()["id"])

    steps = [
        (manager, "triage", {}),
        (manager, "assign", {"contractor_id": wf["contractor"].id}),
        (contractor, "start", {}),
        (contractor, "resolve", {}),
        (tenant, "reopen", {"note": "Still dripping"}),
        (manager, "assign", {"contractor_id": wf["contractor"].id}),
        (contractor, "start", {}),
        (contractor, "resolve", {}),
        (tenant, "close", {}),
    ]
    for client, action, extra in steps:
        assert post_transition(client, req, action, **extra).status_code == 200, action

    resp = tenant.get(f"/api/requests/{req.id}/events/")
    assert resp.status_code == 200
    trail = [(e["from_status"], e["to_status"], e["actor"]) for e in resp.json()["results"]]
    c, m, k = wf["creator"].id, wf["manager"].id, wf["contractor"].id
    assert trail == [
        (None, "submitted", c),
        ("submitted", "triaged", m),
        ("triaged", "assigned", m),
        ("assigned", "in_progress", k),
        ("in_progress", "resolved", k),
        ("resolved", "triaged", c),
        ("triaged", "assigned", m),
        ("assigned", "in_progress", k),
        ("in_progress", "resolved", k),
        ("resolved", "closed", c),
    ]
    assert resp.json()["results"][5]["note"] == "Still dripping"


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_events_cannot_be_modified_through_api(wf, client_for, method):
    req = wf.make()
    RequestEvent.objects.create(request=req, actor=wf["creator"], to_status=S.SUBMITTED)
    client = client_for(wf["manager"])
    resp = getattr(client, method)(f"/api/requests/{req.id}/events/", {}, format="json")
    assert_error(resp, 405, "method_not_allowed")
    assert RequestEvent.objects.filter(request=req).count() == 1
