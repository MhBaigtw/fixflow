"""Scoped throttling: login/refresh per client IP, writes per user."""

import pytest
from rest_framework.throttling import ScopedRateThrottle

from .conftest import assert_error
from .factories import PASSWORD, TenantFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def low_rates(monkeypatch):
    # ScopedRateThrottle reads its rates into a class attribute at import time,
    # so tests patch that attribute rather than settings.
    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {"login": "3/min", "token_refresh": "2/min", "writes": "2/min"},
    )


def assert_throttled(resp):
    error = assert_error(resp, 429, "throttled")
    assert int(resp["Retry-After"]) > 0
    assert error["details"]["retry_after_seconds"] == int(resp["Retry-After"])


def login(client, email, password="wrong", **extra):
    return client.post("/api/auth/token/", {"email": email, "password": password}, **extra)


def test_default_rates_and_shared_cache_settings():
    from config.settings import base

    rates = base.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates == {"login": "10/min", "token_refresh": "10/min", "writes": "60/min"}
    assert base.CACHES["default"]["BACKEND"] == "django.core.cache.backends.db.DatabaseCache"


def test_login_is_throttled_per_ip(anon, low_rates):
    user = TenantFactory()
    for _ in range(3):
        assert login(anon, user.email).status_code == 401
    # Even the right password is refused once the IP is over the limit.
    assert_throttled(login(anon, user.email, PASSWORD))
    # Another client IP has its own bucket.
    assert login(anon, user.email, PASSWORD, REMOTE_ADDR="10.0.0.2").status_code == 200


def test_spoofed_forwarded_for_does_not_bypass_login_limit(anon, low_rates):
    """With NUM_PROXIES=0 (no proxy), X-Forwarded-For is ignored for throttling."""
    user = TenantFactory()
    for i in range(3):
        login(anon, user.email, HTTP_X_FORWARDED_FOR=f"1.2.3.{i}")
    assert_throttled(login(anon, user.email, HTTP_X_FORWARDED_FOR="9.9.9.9"))


def test_behind_one_proxy_client_ip_comes_from_forwarded_for(anon, low_rates, settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    user = TenantFactory()
    for _ in range(3):
        login(anon, user.email, HTTP_X_FORWARDED_FOR="203.0.113.7")
    assert_throttled(login(anon, user.email, HTTP_X_FORWARDED_FOR="203.0.113.7"))
    # Same proxy (REMOTE_ADDR), different real client: separate bucket.
    resp = login(anon, user.email, PASSWORD, HTTP_X_FORWARDED_FOR="198.51.100.9")
    assert resp.status_code == 200


def starter_chain(i, forged=None):
    """X-Forwarded-For as measured on the live Render Starter service (2026-11-17):
    "<client>, <Cloudflare edge>", with REMOTE_ADDR = a rotating Render-internal 10.x
    address. A caller-supplied value is prepended on the left."""
    hops = f"203.0.113.7, 172.70.0.{i}"
    return (f"{forged},{hops}" if forged else hops), f"10.199.0.{i}"


def test_render_starter_chain_keys_on_real_client(anon, low_rates, settings):
    """NUM_PROXIES=2 picks the client even with forged prefixes and rotating proxies."""
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 2}
    user = TenantFactory()
    for i in range(3):
        xff, remote = starter_chain(i, forged=f"6.6.6.{i}")
        login(anon, user.email, REMOTE_ADDR=remote, HTTP_X_FORWARDED_FOR=xff)
    xff, remote = starter_chain(9)
    assert_throttled(login(anon, user.email, REMOTE_ADDR=remote, HTTP_X_FORWARDED_FOR=xff))
    # A different real client behind the same proxies is unaffected.
    resp = login(
        anon,
        user.email,
        PASSWORD,
        REMOTE_ADDR="10.199.0.1",
        HTTP_X_FORWARDED_FOR="198.51.100.9, 172.70.0.1",
    )
    assert resp.status_code == 200


def test_too_many_proxies_lets_forged_header_dodge_the_limit(anon, low_rates, settings):
    """Documents the 2026-11-17 production bug: the free plan's chain had one more hop,
    so NUM_PROXIES=3 was right there. On Starter it makes DRF key on the forged
    left-most entry, and a fresh fake IP per request never hits the limit."""
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 3}
    user = TenantFactory()
    codes = []
    for i in range(5):
        xff, remote = starter_chain(i, forged=f"6.6.6.{i}")
        codes.append(
            login(anon, user.email, REMOTE_ADDR=remote, HTTP_X_FORWARDED_FOR=xff).status_code
        )
    assert 429 not in codes


def test_refresh_is_throttled(anon, low_rates):
    user = TenantFactory()
    refresh = login(anon, user.email, PASSWORD).json()["refresh"]
    for _ in range(2):
        assert anon.post("/api/auth/token/refresh/", {"refresh": refresh}).status_code == 200
    assert_throttled(anon.post("/api/auth/token/refresh/", {"refresh": refresh}))


def test_writes_are_throttled_per_user_and_reads_are_not(wf, client_for, low_rates):
    req = wf.make()
    creator = client_for(wf["creator"])
    url = f"/api/requests/{req.id}/comments/"
    assert creator.post(url, {"body": "1"}).status_code == 201
    assert creator.patch(f"/api/requests/{req.id}/", {"title": "x"}).status_code == 200
    assert_throttled(creator.post(url, {"body": "3"}))
    assert_throttled(
        creator.post(f"/api/requests/{req.id}/transition/", {"action": "cancel"}, format="json")
    )
    # Reads are never throttled.
    for _ in range(5):
        assert creator.get(url).status_code == 200
    # A different user has their own write budget.
    assert client_for(wf["manager"]).post(url, {"body": "m"}).status_code == 201


def test_create_counts_as_write(wf, client_for, low_rates):
    client = client_for(wf["creator"])
    data = {
        "unit": wf["unit"].id,
        "title": "t",
        "description": "d",
        "category": "other",
        "priority": "routine",
    }
    for _ in range(2):
        assert client.post("/api/requests/", data, format="json").status_code == 201
    assert_throttled(client.post("/api/requests/", data, format="json"))
