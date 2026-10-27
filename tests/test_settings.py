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
    assert prod.SECURE_REDIRECT_EXEMPT == [r"^api/health/$"]


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 60)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example.com:5432/app")
    for var in ("RENDER_EXTERNAL_HOSTNAME", "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("DB_SSLMODE", raising=False)
    monkeypatch.delenv("DB_CONN_MAX_AGE", raising=False)
    return monkeypatch


def test_prod_database_requires_tls_and_reuses_connections(prod_env, fresh_prod):
    db = fresh_prod().DATABASES["default"]
    assert db["OPTIONS"]["sslmode"] == "require"
    assert db["CONN_MAX_AGE"] == 60
    assert db["CONN_HEALTH_CHECKS"] is True


def test_prod_keeps_sslmode_from_url_and_allows_override(prod_env, fresh_prod):
    prod_env.setenv("DATABASE_URL", "postgres://u:p@db.example.com/app?sslmode=verify-full")
    assert fresh_prod().DATABASES["default"]["OPTIONS"]["sslmode"] == "verify-full"


def test_prod_db_sslmode_env_override(prod_env, fresh_prod):
    prod_env.setenv("DB_SSLMODE", "disable")
    prod_env.setenv("DB_CONN_MAX_AGE", "0")
    db = fresh_prod().DATABASES["default"]
    assert db["OPTIONS"]["sslmode"] == "disable"
    assert db["CONN_MAX_AGE"] == 0


def test_prod_hosts_and_csrf_origins(prod_env, fresh_prod):
    prod_env.setenv("ALLOWED_HOSTS", "api.example.com")
    prod_env.setenv("CSRF_TRUSTED_ORIGINS", "https://api.example.com")
    prod_env.setenv("RENDER_EXTERNAL_HOSTNAME", "fixflow.onrender.com")
    prod = fresh_prod()
    assert prod.ALLOWED_HOSTS == ["api.example.com", "fixflow.onrender.com"]
    assert prod.CSRF_TRUSTED_ORIGINS == [
        "https://api.example.com",
        "https://fixflow.onrender.com",
    ]


def test_prod_without_render_host_uses_env_only(prod_env, fresh_prod):
    prod = fresh_prod()
    assert prod.ALLOWED_HOSTS == []
    assert prod.CSRF_TRUSTED_ORIGINS == []
