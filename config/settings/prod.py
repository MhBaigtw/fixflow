from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import DATABASES, env

# No default: a missing SECRET_KEY raises ImproperlyConfigured at startup.
SECRET_KEY = env("SECRET_KEY")
DEBUG = False

# Hosts: ALLOWED_HOSTS from env, plus the hostname Render assigns (set automatically
# on Render), so the service works before a custom domain exists.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
_render_host = env("RENDER_EXTERNAL_HOSTNAME", default="")
if _render_host:
    ALLOWED_HOSTS.append(_render_host)
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

# Database: TLS required by default (set DB_SSLMODE=disable only for the local
# docker-compose Postgres). Live: Supabase *session* pooler (port 5432), where each
# client connection gets its own server connection, so transactions and
# SELECT ... FOR UPDATE behave as on a direct connection. Never the transaction pooler
# (port 6543): see docs/decisions.md D28. Connections are reused for DB_CONN_MAX_AGE
# seconds; CONN_HEALTH_CHECKS replaces any connection the pooler closed while idle.
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"].setdefault("sslmode", env("DB_SSLMODE", default="require"))
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
if (
    str(DATABASES["default"].get("HOST", "")).endswith(".pooler.supabase.com")
    and str(DATABASES["default"].get("PORT", "")) == "6543"
):
    raise ImproperlyConfigured(
        "DATABASE_URL points at Supabase's transaction pooler (port 6543). Use the session "
        "pooler (port 5432): row locks, persistent connections and prepared statements "
        "need a session (docs/decisions.md D28)."
    )

# TLS is terminated by the platform proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
# Platform health checks use plain HTTP: liveness (no DB) and readiness (DB check).
SECURE_REDIRECT_EXEMPT = [r"^api/health/$", r"^api/health/live/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
