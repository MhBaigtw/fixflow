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


def test_render_chain_keys_on_real_client(anon, low_rates, settings):
    """The X-Forwarded-For shape measured on the live Render service:
    "<client>, <Cloudflare edge>, <Render internal>", with both proxy hops changing on
    every request and REMOTE_ADDR=127.0.0.1. NUM_PROXIES=3 must pick the client, even
    when the caller prepends forged entries."""
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 3}
    user = TenantFactory()

    def chain(i, forged=None):
        hops = f"203.0.113.7, 172.71.0.{i}, 10.192.0.{i}"
        return f"{forged}, {hops}" if forged else hops

    for i in range(3):
        login(
            anon, user.email, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=chain(i, f"6.6.6.{i}")
        )
    assert_throttled(
        login(anon, user.email, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=chain(9))
    )
    # A different real client behind the same proxies is unaffected.
    other = "198.51.100.9, 172.71.0.1, 10.192.0.1"
    resp = login(anon, user.email, PASSWORD, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=other)
    assert resp.status_code == 200


def test_too_few_proxies_lets_rotating_hops_dodge_the_limit(anon, low_rates, settings):
    """Documents the production bug: with NUM_PROXIES=2 the key is the Cloudflare hop,
    which changes per request, so the limit never triggers."""
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 2}
    user = TenantFactory()
    codes = [
        login(
            anon,
            user.email,
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR=f"203.0.113.7, 172.71.0.{i}, 10.192.0.{i}",
        ).status_code
        for i in range(5)
    ]
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
