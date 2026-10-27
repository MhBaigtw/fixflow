"""SLA due dates, the overdue filter, list filters and ordering.

Overdue tests use explicit timestamps (hours in the past/future) rather than
freezing time, because the overdue filter compares against the database's NOW().
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.maintenance.models import MaintenanceRequest
from apps.maintenance.services.requests import create_request

from .conftest import assert_error
from .factories import UnitFactory

pytestmark = pytest.mark.django_db
S = MaintenanceRequest.Status


def ids(resp):
    assert resp.status_code == 200, resp.content
    return [r["id"] for r in resp.json()["results"]]


@pytest.mark.parametrize("priority,hours", [("emergency", 24), ("urgent", 72), ("routine", 7 * 24)])
def test_sla_due_at_per_priority(wf, priority, hours):
    req = create_request(
        tenant=wf["creator"],
        unit=wf["unit"],
        title="t",
        description="d",
        category="other",
        priority=priority,
    )
    assert req.sla_due_at - req.created_at == timedelta(hours=hours)


def test_sla_targets_come_from_settings(wf, settings):
    settings.MAINTENANCE_SLA_HOURS = {"emergency": 2, "urgent": 3, "routine": 4}
    req = create_request(
        tenant=wf["creator"],
        unit=wf["unit"],
        title="t",
        description="d",
        category="other",
        priority="emergency",
    )
    assert req.sla_due_at - req.created_at == timedelta(hours=2)


def test_priority_change_recomputes_sla_from_created_at(wf, client_for):
    created = timezone.now() - timedelta(hours=10)
    req = wf.make(priority="routine", created_at=created, sla_due_at=created + timedelta(days=7))
    resp = client_for(wf["creator"]).patch(
        f"/api/requests/{req.id}/", {"priority": "emergency"}, format="json"
    )
    assert resp.status_code == 200
    req.refresh_from_db()
    assert req.sla_due_at == created + timedelta(hours=24)
    assert resp.json()["is_overdue"] is False


@pytest.fixture
def sla_mix(wf):
    past = timezone.now() - timedelta(hours=1)
    future = timezone.now() + timedelta(hours=1)
    return {
        "late_submitted": wf.make(S.SUBMITTED, sla_due_at=past),
        "late_in_progress": wf.make(S.IN_PROGRESS, sla_due_at=past),
        "on_time": wf.make(S.SUBMITTED, sla_due_at=future),
        "late_but_resolved": wf.make(S.RESOLVED, sla_due_at=past),
        "late_but_closed": wf.make(S.CLOSED, sla_due_at=past),
        "late_but_cancelled": wf.make(S.CANCELLED, sla_due_at=past),
    }


def test_overdue_filter(wf, sla_mix, client_for):
    client = client_for(wf["manager"])
    overdue = {sla_mix["late_submitted"].id, sla_mix["late_in_progress"].id}
    assert set(ids(client.get("/api/requests/?overdue=true"))) == overdue
    not_overdue = {r.id for r in sla_mix.values()} - overdue
    assert set(ids(client.get("/api/requests/?overdue=false"))) == not_overdue


def test_is_overdue_field_matches_filter(wf, sla_mix, client_for):
    results = client_for(wf["manager"]).get("/api/requests/?page_size=100").json()["results"]
    flagged = {r["id"] for r in results if r["is_overdue"]}
    assert flagged == {sla_mix["late_submitted"].id, sla_mix["late_in_progress"].id}


@pytest.fixture
def mix(wf):
    other_unit = wf["other_unit"]
    now = timezone.now()
    return {
        "a": wf.make(
            S.SUBMITTED,
            priority="routine",
            category="plumbing",
            created_at=now - timedelta(days=3),
            sla_due_at=now + timedelta(days=4),
        ),
        "b": wf.make(
            S.ASSIGNED,
            priority="emergency",
            category="electrical",
            created_at=now - timedelta(days=2),
            sla_due_at=now + timedelta(days=1),
        ),
        "c": wf.make(
            S.TRIAGED,
            unit=other_unit,
            created_by=wf["outsider"],
            priority="urgent",
            category="plumbing",
            created_at=now - timedelta(days=1),
            sla_due_at=now + timedelta(days=2),
        ),
    }


@pytest.mark.parametrize(
    "query,expected",
    [
        ("status=submitted", {"a"}),
        ("status=assigned", {"b"}),
        ("priority=urgent", {"c"}),
        ("category=plumbing", {"a", "c"}),
        ("category=plumbing&priority=routine", {"a"}),
        ("unit={unit}", {"a", "b"}),
        ("unit={other_unit}", {"c"}),
        ("property={property}", {"a", "b", "c"}),
        ("property=999999", set()),
        ("assigned_to={contractor}", {"b"}),
    ],
)
def test_list_filters(wf, mix, client_for, query, expected):
    query = query.format(
        unit=wf["unit"].id,
        other_unit=wf["other_unit"].id,
        property=wf["property"].id,
        contractor=wf["contractor"].id,
    )
    got = set(ids(client_for(wf["manager"]).get(f"/api/requests/?{query}")))
    assert got == {mix[k].id for k in expected}


def test_created_after_and_before(wf, mix, client_for):
    client = client_for(wf["manager"])
    cutoff = (timezone.now() - timedelta(days=2, hours=12)).isoformat()
    after = set(ids(client.get("/api/requests/", {"created_after": cutoff})))
    before = set(ids(client.get("/api/requests/", {"created_before": cutoff})))
    assert after == {mix["b"].id, mix["c"].id}
    assert before == {mix["a"].id}


@pytest.mark.parametrize("query", ["status=nope", "priority=asap", "overdue=maybe"])
def test_invalid_filter_values_are_400(wf, client_for, query):
    resp = client_for(wf["manager"]).get(f"/api/requests/?{query}")
    assert_error(resp, 400, "validation_error")


def test_filters_never_widen_scope(wf, mix, client_for):
    # An outsider filtering by a unit they don't rent sees nothing, not someone else's data.
    resp = client_for(wf["outsider"]).get(f"/api/requests/?unit={wf['unit'].id}")
    assert ids(resp) == []


@pytest.mark.parametrize(
    "ordering,expected",
    [
        ("priority", ["b", "c", "a"]),  # emergency, urgent, routine (not alphabetical)
        ("-priority", ["a", "c", "b"]),
        ("created_at", ["a", "b", "c"]),
        ("-created_at", ["c", "b", "a"]),
        ("sla_due_at", ["b", "c", "a"]),
        (None, ["c", "b", "a"]),  # default: newest first
    ],
)
def test_ordering(wf, mix, client_for, ordering, expected):
    url = "/api/requests/" + (f"?ordering={ordering}" if ordering else "")
    assert ids(client_for(wf["manager"]).get(url)) == [mix[k].id for k in expected]


def test_list_is_paginated(wf, client_for):
    unit = UnitFactory(property=wf["property"])
    for _ in range(25):
        wf.make(unit=unit)
    body = client_for(wf["manager"]).get("/api/requests/").json()
    assert body["count"] == 25
    assert len(body["results"]) == 20
    assert body["next"]
