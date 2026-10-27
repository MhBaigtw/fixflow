import importlib
import sys

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured


@pytest.fixture
def fresh_prod(monkeypatch):
    """Import config.settings.prod from scratch, ignoring any local .env file."""
    monkeypatch.setattr(environ.Env, "read_env", staticmethod(lambda *a, **k: None))
    for name in ("config.settings.prod", "config.settings.base"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return lambda: importlib.import_module("config.settings.prod")


def test_prod_refuses_to_start_without_secret_key(monkeypatch, fresh_prod):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ImproperlyConfigured):
        fresh_prod()


def test_prod_forces_secure_settings(monkeypatch, fresh_prod):
    monkeypatch.setenv("SECRET_KEY", "x" * 60)
    monkeypatch.setenv("DEBUG", "True")
    prod = fresh_prod()
    assert prod.DEBUG is False
    assert prod.SESSION_COOKIE_SECURE and prod.CSRF_COOKIE_SECURE
    assert prod.SECURE_HSTS_SECONDS >= 31536000
    assert prod.SECURE_SSL_REDIRECT is True
