"""Permission matrix and business rules for properties, units and tenancies.

Owner OK, other manager 404, tenant/contractor 403, anonymous 401.
"""

import pytest

from apps.properties.models import Property, Tenancy

from .conftest import assert_error
from .factories import (
    ContractorFactory,
    ManagerFactory,
    PropertyFactory,
    TenancyFactory,
    TenantFactory,
    UnitFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    owner = ManagerFactory()
    prop = PropertyFactory(manager=owner)
    unit = UnitFactory(property=prop)
    tenancy = TenancyFactory(unit=unit)
    return {
        "owner": owner,
        "other_manager": ManagerFactory(),
        "tenant": TenantFactory(),
        "contractor": ContractorFactory(),
        "property": prop,
        "unit": unit,
        "tenancy": tenancy,
    }


def endpoints(w):
    """(method, url, payload, success status) for every Phase 1 object endpoint."""
    new_tenant = TenantFactory()
    return [
        ("get", f"/api/properties/{w['property'].id}/", None, 200),
        ("patch", f"/api/properties/{w['property'].id}/", {"name": "Renamed"}, 200),
        ("put", f"/api/properties/{w['property'].id}/", {"name": "N", "address": "A"}, 200),
        ("get", f"/api/properties/{w['property'].id}/units/", None, 200),
        ("post", f"/api/properties/{w['property'].id}/units/", {"label": "NEW"}, 201),
        ("get", f"/api/units/{w['unit'].id}/tenancies/", None, 200),
        ("post", f"/api/units/{w['unit'].id}/tenancies/", {"email": new_tenant.email}, 201),
        ("post", f"/api/tenancies/{w['tenancy'].id}/end/", None, 200),
    ]


@pytest.mark.parametrize("index", range(8))
@pytest.mark.parametrize(
    "who,expected",
    [("owner", None), ("other_manager", 404), ("tenant", 403), ("contractor", 403), ("anon", 401)],
)
def test_permission_matrix(world, client_for, anon, index, who, expected):
    method, url, payload, success = endpoints(world)[index]
    client = anon if who == "anon" else client_for(world[who])
    resp = getattr(client, method)(url, payload, format="json")
    if expected is None:
        assert resp.status_code == success, resp.content
    else:
        assert_error(resp, expected)


@pytest.mark.parametrize("who,expected", [("tenant", 403), ("contractor", 403), ("anon", 401)])
def test_property_list_and_create_require_manager(world, client_for, anon, who, expected):
    client = anon if who == "anon" else client_for(world[who])
    assert_error(client.get("/api/properties/"), expected)
    assert_error(client.post("/api/properties/", {"name": "x", "address": "y"}), expected)


def test_manager_lists_only_own_properties(world, client_for):
    PropertyFactory(manager=world["other_manager"])
    resp = client_for(world["owner"]).get("/api/properties/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert [p["id"] for p in body["results"]] == [world["property"].id]


def test_manager_creates_property_owned_by_self(world, client_for):
    other = world["other_manager"]
    resp = client_for(world["owner"]).post(
        "/api/properties/", {"name": "New", "address": "1 Road", "manager": other.id}
    )
    assert resp.status_code == 201
    assert Property.objects.get(pk=resp.json()["id"]).manager == world["owner"]


def test_property_delete_not_allowed(world, client_for):
    resp = client_for(world["owner"]).delete(f"/api/properties/{world['property'].id}/")
    assert_error(resp, 405, "method_not_allowed")


def test_duplicate_unit_label_rejected(world, client_for):
    url = f"/api/properties/{world['property'].id}/units/"
    resp = client_for(world["owner"]).post(url, {"label": world["unit"].label})
    assert_error(resp, 400, "validation_error")["details"]["label"]


def test_attach_tenant_by_email(world, client_for):
    tenant = TenantFactory(email="roomie@example.com")
    resp = client_for(world["owner"]).post(
        f"/api/units/{world['unit'].id}/tenancies/",
        {"email": "ROOMIE@example.com", "start_date": "2026-01-01"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tenant"] == tenant.id
    assert body["start_date"] == "2026-01-01"
    assert body["is_active"] is True
    # Roommates: two active tenants on the same unit is fine.
    assert Tenancy.objects.filter(unit=world["unit"], end_date__isnull=True).count() == 2


@pytest.mark.parametrize("factory", [ManagerFactory, ContractorFactory])
def test_attach_rejects_non_tenant_role(world, client_for, factory):
    user = factory()
    resp = client_for(world["owner"]).post(
        f"/api/units/{world['unit'].id}/tenancies/", {"email": user.email}
    )
    assert "email" in assert_error(resp, 400, "validation_error")["details"]


def test_attach_rejects_unknown_email(world, client_for):
    resp = client_for(world["owner"]).post(
        f"/api/units/{world['unit'].id}/tenancies/", {"email": "nobody@example.com"}
    )
    assert_error(resp, 400, "validation_error")


def test_attach_rejects_duplicate_active_tenancy(world, client_for):
    existing = world["tenancy"].tenant
    resp = client_for(world["owner"]).post(
        f"/api/units/{world['unit'].id}/tenancies/", {"email": existing.email}
    )
    assert "email" in assert_error(resp, 400, "validation_error")["details"]


def test_reattach_after_ending_is_allowed(world, client_for):
    client = client_for(world["owner"])
    tenancy = world["tenancy"]
    assert client.post(f"/api/tenancies/{tenancy.id}/end/").status_code == 200
    resp = client.post(f"/api/units/{world['unit'].id}/tenancies/", {"email": tenancy.tenant.email})
    assert resp.status_code == 201


def test_end_tenancy_sets_end_date_and_is_not_repeatable(world, client_for):
    client = client_for(world["owner"])
    url = f"/api/tenancies/{world['tenancy'].id}/end/"
    resp = client.post(url)
    assert resp.status_code == 200
    assert resp.json()["end_date"] is not None
    assert resp.json()["is_active"] is False
    assert_error(client.post(url), 409, "tenancy_already_ended")


def test_db_constraint_blocks_duplicate_active_tenancy(world):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        TenancyFactory(unit=world["unit"], tenant=world["tenancy"].tenant)


def test_attach_race_maps_integrity_error_to_400(world, monkeypatch):
    """If two attaches race past the exists() check, the DB constraint still wins."""
    from rest_framework.exceptions import ValidationError

    from apps.properties import services

    tenant = world["tenancy"].tenant
    real_filter = Tenancy.objects.filter

    class NoExists:
        def __init__(self, qs):
            self.qs = qs

        def exists(self):
            return False

    monkeypatch.setattr(Tenancy.objects, "filter", lambda *a, **kw: NoExists(real_filter(*a, **kw)))
    with pytest.raises(ValidationError):
        services.attach_tenant(unit=world["unit"], email=tenant.email)


def test_model_str(world):
    assert str(world["property"]) == world["property"].name
    assert world["unit"].label in str(world["unit"])
    assert world["tenancy"].tenant.email in str(world["tenancy"])
