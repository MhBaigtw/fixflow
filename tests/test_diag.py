"""TEMPORARY diagnostic endpoint (remove together with the view)."""

import pytest

from .conftest import assert_error
from .factories import TenantFactory

pytestmark = pytest.mark.django_db


def test_requires_auth(anon):
    assert_error(anon.get("/api/_diag/client-ip/"), 401)


def test_reports_callers_own_chain(client_for):
    resp = client_for(TenantFactory()).get(
        "/api/_diag/client-ip/", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2", REMOTE_ADDR="10.0.0.9"
    )
    body = resp.json()
    assert body["remote_addr"] == "10.0.0.9"
    assert body["x_forwarded_for"] == "1.1.1.1, 2.2.2.2"
    assert body["num_proxies"] == 0
    assert body["throttle_ident"] == "10.0.0.9"
