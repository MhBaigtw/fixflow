"""Error shape per status code, health, schema and docs."""

from unittest import mock

import pytest
from django.db import OperationalError
from django.test import Client

from .conftest import assert_error
from .factories import ManagerFactory, TenantFactory

pytestmark = pytest.mark.django_db


def test_error_shape_400(client_for):
    resp = client_for(ManagerFactory()).post("/api/properties/", {}, format="json")
    error = assert_error(resp, 400, "validation_error")
    assert set(error["details"]) == {"name", "address"}


def test_error_shape_401(anon):
    assert_error(anon.get("/api/properties/"), 401, "not_authenticated")


def test_error_shape_403(client_for):
    assert_error(client_for(TenantFactory()).get("/api/properties/"), 403, "permission_denied")


def test_error_shape_404(client_for):
    assert_error(client_for(ManagerFactory()).get("/api/properties/999999/"), 404, "not_found")


def test_error_shape_405(client_for):
    assert_error(client_for(ManagerFactory()).delete("/api/me/"), 405, "method_not_allowed")


def test_error_shape_409(client_for):
    from .factories import TenancyFactory

    tenancy = TenancyFactory(end_date="2026-01-31")
    client = client_for(tenancy.unit.property.manager)
    assert_error(client.post(f"/api/tenancies/{tenancy.id}/end/"), 409, "tenancy_already_ended")


def test_unknown_url_returns_json_404():
    resp = Client().get("/api/does-not-exist/")
    assert_error(resp, 404, "not_found")


def test_server_error_handler_shape():
    from apps.core.views import server_error

    resp = server_error(None)
    assert resp.status_code == 500
    assert b'"server_error"' in resp.content


def test_django_permission_denied_is_mapped():
    from django.core.exceptions import PermissionDenied

    from apps.core.exceptions import exception_handler

    resp = exception_handler(PermissionDenied(), {})
    assert resp.status_code == 403
    assert resp.data["error"]["code"] == "permission_denied"


def test_unhandled_exception_falls_through():
    from apps.core.exceptions import exception_handler

    assert exception_handler(RuntimeError("boom"), {}) is None


def test_health_ok(anon):
    resp = anon.get("/api/health/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_db_down(anon):
    with mock.patch("apps.core.views.connection.cursor", side_effect=OperationalError("db down")):
        resp = anon.get("/api/health/")
    error = assert_error(resp, 503, "service_unavailable")
    assert error["details"] == {"database": "unavailable"}


def test_health_ignores_bad_token(anon):
    anon.credentials(HTTP_AUTHORIZATION="Bearer garbage")
    assert anon.get("/api/health/").status_code == 200


def test_schema_and_docs(anon):
    schema = anon.get("/api/schema/")
    assert schema.status_code == 200
    assert b"openapi: 3" in schema.content
    docs = anon.get("/api/docs/")
    assert docs.status_code == 200
    assert b"swagger" in docs.content.lower()


def test_pagination_defaults(client_for):
    from .factories import PropertyFactory

    manager = ManagerFactory()
    PropertyFactory.create_batch(25, manager=manager)
    client = client_for(manager)
    body = client.get("/api/properties/").json()
    assert body["count"] == 25
    assert len(body["results"]) == 20
    assert body["next"] is not None
    assert len(client.get("/api/properties/?page_size=500").json()["results"]) == 25
