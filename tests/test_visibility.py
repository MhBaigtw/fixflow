"""Who sees which requests, internal comments, creation and edit rules."""

import pytest
from django.utils import timezone

from apps.maintenance.models import Comment, MaintenanceRequest

from .conftest import assert_error
from .factories import PropertyFactory, TenancyFactory, UnitFactory

pytestmark = pytest.mark.django_db
S = MaintenanceRequest.Status


def ids(resp):
    assert resp.status_code == 200, resp.content
    return {r["id"] for r in resp.json()["results"]}


@pytest.fixture
def spread(wf):
    """Requests spread across units, properties and contractors."""
    other_prop = PropertyFactory(manager=wf["other_manager"])
    far_unit = UnitFactory(property=other_prop)
    far_tenant = TenancyFactory(unit=far_unit).tenant
    return {
        "mine": wf.make(S.SUBMITTED),
        "roommates": wf.make(S.ASSIGNED, created_by=wf["roommate"]),
        "neighbour": wf.make(S.SUBMITTED, unit=wf["other_unit"], created_by=wf["outsider"]),
        "other_contractor": wf.make(
            S.IN_PROGRESS,
            unit=wf["other_unit"],
            created_by=wf["outsider"],
            assigned_to=wf["other_contractor"],
        ),
        "far": wf.make(S.SUBMITTED, unit=far_unit, created_by=far_tenant),
    }


@pytest.mark.parametrize(
    "actor,expected",
    [
        ("creator", {"mine", "roommates"}),
        ("roommate", {"mine", "roommates"}),
        ("outsider", {"neighbour", "other_contractor"}),
        ("manager", {"mine", "roommates", "neighbour", "other_contractor"}),
        ("other_manager", {"far"}),
        ("contractor", {"roommates"}),
        ("other_contractor", {"other_contractor"}),
    ],
)
def test_each_role_sees_exactly_its_requests(wf, spread, client_for, actor, expected):
    got = ids(client_for(wf[actor]).get("/api/requests/"))
    assert got == {spread[name].id for name in expected}


def test_list_requires_auth(anon):
    assert_error(anon.get("/api/requests/"), 401, "not_authenticated")


@pytest.mark.parametrize(
    "method,suffix,payload",
    [
        ("get", "", None),
        ("patch", "", {"title": "x"}),
        ("get", "events/", None),
        ("get", "comments/", None),
        ("post", "comments/", {"body": "hi"}),
        ("post", "transition/", {"action": "cancel"}),
    ],
)
def test_tenant_from_other_unit_gets_404_everywhere(wf, client_for, method, suffix, payload):
    req = wf.make(S.SUBMITTED)
    client = client_for(wf["outsider"])
    resp = getattr(client, method)(f"/api/requests/{req.id}/{suffix}", payload, format="json")
    assert_error(resp, 404, "not_found")


def test_roommate_sees_request_they_did_not_create(wf, client_for):
    req = wf.make()
    resp = client_for(wf["roommate"]).get(f"/api/requests/{req.id}/")
    assert resp.status_code == 200
    assert resp.json()["created_by"] == wf["creator"].id


def test_ended_tenancy_loses_visibility(wf, client_for):
    req = wf.make()
    client = client_for(wf["creator"])
    assert client.get(f"/api/requests/{req.id}/").status_code == 200
    wf["creator"].tenancies.update(end_date=timezone.localdate())
    assert ids(client.get("/api/requests/")) == set()
    assert_error(client.get(f"/api/requests/{req.id}/"), 404)


def test_detail_shape(wf, client_for):
    req = wf.make(S.ASSIGNED)
    body = client_for(wf["creator"]).get(f"/api/requests/{req.id}/").json()
    assert body["property"] == wf["property"].id
    assert body["unit"] == wf["unit"].id
    assert body["assigned_to"] == wf["contractor"].id
    assert body["is_overdue"] is False


# --- Comments ------------------------------------------------------------------


@pytest.fixture
def comments(wf):
    req = wf.make(S.ASSIGNED)
    public = Comment.objects.create(request=req, author=wf["creator"], body="Any update?")
    internal = Comment.objects.create(
        request=req, author=wf["manager"], body="Vendor is slow", is_internal=True
    )
    return req, public, internal


@pytest.mark.parametrize(
    "actor,sees_internal",
    [("creator", False), ("roommate", False), ("manager", True), ("contractor", True)],
)
def test_internal_comments_hidden_from_tenants_in_list(
    wf, comments, client_for, actor, sees_internal
):
    req, public, internal = comments
    got = ids(client_for(wf[actor]).get(f"/api/requests/{req.id}/comments/"))
    assert got == ({public.id, internal.id} if sees_internal else {public.id})


@pytest.mark.parametrize("actor,status", [("creator", 404), ("manager", 200), ("contractor", 200)])
def test_internal_comment_detail(wf, comments, client_for, actor, status):
    req, public, internal = comments
    client = client_for(wf[actor])
    assert client.get(f"/api/requests/{req.id}/comments/{public.id}/").status_code == 200
    resp = client.get(f"/api/requests/{req.id}/comments/{internal.id}/")
    if status == 404:
        assert_error(resp, 404, "not_found")
    else:
        assert resp.json()["is_internal"] is True


def test_comment_detail_must_belong_to_request(wf, comments, client_for):
    _, public, _ = comments
    other = wf.make()
    resp = client_for(wf["manager"]).get(f"/api/requests/{other.id}/comments/{public.id}/")
    assert_error(resp, 404)


def test_tenant_cannot_post_internal_comment(wf, comments, client_for):
    req, _, _ = comments
    resp = client_for(wf["creator"]).post(
        f"/api/requests/{req.id}/comments/", {"body": "psst", "is_internal": True}, format="json"
    )
    assert_error(resp, 403, "permission_denied")
    assert not Comment.objects.filter(body="psst").exists()


@pytest.mark.parametrize(
    "actor,is_internal",
    [("creator", False), ("manager", True), ("contractor", True), ("contractor", False)],
)
def test_post_comment(wf, comments, client_for, actor, is_internal):
    req, _, _ = comments
    resp = client_for(wf[actor]).post(
        f"/api/requests/{req.id}/comments/",
        {"body": "note", "is_internal": is_internal},
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["author"] == wf[actor].id
    assert body["is_internal"] is is_internal


def test_empty_comment_rejected(wf, comments, client_for):
    req, _, _ = comments
    resp = client_for(wf["creator"]).post(f"/api/requests/{req.id}/comments/", {"body": ""})
    assert_error(resp, 400, "validation_error")


# --- Creation ------------------------------------------------------------------


def payload(unit, **overrides):
    data = {
        "unit": unit.id,
        "title": "Broken heater",
        "description": "No heat in bedroom.",
        "category": "heating",
        "priority": "urgent",
    }
    data.update(overrides)
    return data


def test_tenant_creates_request_with_initial_event(wf, client_for):
    resp = client_for(wf["creator"]).post("/api/requests/", payload(wf["unit"]), format="json")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["created_by"] == wf["creator"].id
    assert body["assigned_to"] is None
    req = MaintenanceRequest.objects.get(pk=body["id"])
    event = req.events.get()
    assert (event.from_status, event.to_status, event.actor) == (None, "submitted", wf["creator"])


@pytest.mark.parametrize("actor,status", [("manager", 403), ("contractor", 403)])
def test_only_tenants_create(wf, client_for, actor, status):
    resp = client_for(wf[actor]).post("/api/requests/", payload(wf["unit"]), format="json")
    assert_error(resp, status, "permission_denied")


def test_anonymous_cannot_create(wf, anon):
    assert_error(anon.post("/api/requests/", payload(wf["unit"]), format="json"), 401)


@pytest.mark.parametrize("unit_key", ["other_unit", "missing"])
def test_tenant_cannot_create_on_unit_they_do_not_rent(wf, client_for, unit_key):
    data = payload(wf["unit"])
    data["unit"] = 999999 if unit_key == "missing" else wf[unit_key].id
    resp = client_for(wf["creator"]).post("/api/requests/", data, format="json")
    error = assert_error(resp, 400, "validation_error")
    # Same message whether the unit exists or not: no existence leak.
    assert "does not exist" in error["details"]["unit"][0]


def test_tenant_with_ended_tenancy_cannot_create(wf, client_for):
    wf["creator"].tenancies.update(end_date=timezone.localdate())
    resp = client_for(wf["creator"]).post("/api/requests/", payload(wf["unit"]), format="json")
    assert_error(resp, 400, "validation_error")


def test_create_service_enforces_tenancy(wf):
    from rest_framework.exceptions import ValidationError

    from apps.maintenance.services.requests import create_request

    with pytest.raises(ValidationError):
        create_request(
            tenant=wf["outsider"],
            unit=wf["unit"],
            title="t",
            description="d",
            category="other",
            priority="routine",
        )


@pytest.mark.parametrize("field,value", [("category", "magic"), ("priority", "asap")])
def test_create_rejects_bad_choices(wf, client_for, field, value):
    data = payload(wf["unit"], **{field: value})
    resp = client_for(wf["creator"]).post("/api/requests/", data, format="json")
    assert field in assert_error(resp, 400, "validation_error")["details"]


# --- Edit rules ----------------------------------------------------------------


def test_creator_edits_while_submitted(wf, client_for):
    req = wf.make()
    resp = client_for(wf["creator"]).patch(
        f"/api/requests/{req.id}/",
        {"title": "New title", "description": "More detail", "category": "electrical"},
        format="json",
    )
    assert resp.status_code == 200
    req.refresh_from_db()
    assert (req.title, req.description, req.category) == ("New title", "More detail", "electrical")


# The manager's PATCH rules (priority only) are tested in test_edits.py.
@pytest.mark.parametrize(
    "actor,status", [("roommate", 403), ("outsider", 404), ("contractor", 404)]
)
def test_only_creator_edits(wf, client_for, actor, status):
    req = wf.make()
    resp = client_for(wf[actor]).patch(f"/api/requests/{req.id}/", {"title": "x"}, format="json")
    assert_error(resp, status)
    req.refresh_from_db()
    assert req.title != "x"


@pytest.mark.parametrize("status", [S.TRIAGED, S.CANCELLED, S.RESOLVED])
def test_edit_only_while_submitted(wf, client_for, status):
    req = wf.make(status)
    resp = client_for(wf["creator"]).patch(f"/api/requests/{req.id}/", {"title": "x"})
    error = assert_error(resp, 409, "request_not_editable")
    assert error["details"] == {"current_status": status}


@pytest.mark.parametrize(
    "data", [{"status": "closed"}, {"title": "ok", "assigned_to": 1}, {"unit": 1}, {}]
)
def test_edit_rejects_non_editable_fields(wf, client_for, data):
    req = wf.make()
    resp = client_for(wf["creator"]).patch(f"/api/requests/{req.id}/", data, format="json")
    assert_error(resp, 400, "validation_error")
    req.refresh_from_db()
    assert req.status == S.SUBMITTED


@pytest.mark.parametrize("method", ["put", "delete"])
def test_put_and_delete_not_allowed(wf, client_for, method):
    req = wf.make()
    resp = getattr(client_for(wf["creator"]), method)(f"/api/requests/{req.id}/", {})
    assert_error(resp, 405, "method_not_allowed")
