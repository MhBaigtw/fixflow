# FixFlow

A backend-only REST API for rental maintenance requests. Tenants report problems,
property managers triage them and assign contractors, contractors do the work, and
tenants confirm the fix or reopen the request. Every status change is audited.

**Status: Phase 2 of 4 complete** (foundation + core workflow). Phase 3 (benchmarks,
query optimization, throttling, stats) and Phase 4 (deployment) are not started. See
[SPEC.md](SPEC.md) and [TASKS.md](TASKS.md).

## The problem it solves

Landlords track repairs over text messages, so requests get lost, nobody knows who
is responsible, and nothing proves when work was done. FixFlow puts each request on a
strict workflow with an append-only audit trail. Each person sees only the requests
they're entitled to.

## What's interesting in the code

- **Strict state machine:** all status changes go through one transition table
  ([apps/maintenance/services/transitions.py](apps/maintenance/services/transitions.py)).
  A move that isn't allowed from the current status returns `409 invalid_transition`.
  An allowed move by the wrong person returns `403`.
- **Row locking:** each transition runs in a transaction, locks the row with
  `SELECT ... FOR UPDATE`, re-checks the status, then writes the change and its audit
  event together. A real-thread test proves that when two transitions race on one
  request, exactly one wins (and that the test fails if the lock is removed).
- **No cross-tenant leaks:** every lookup goes through scoped selectors, so a request
  outside your scope is a `404` (its existence is never confirmed), never a `403`.
  Roommates share visibility. Ending a tenancy removes it.
- **One error shape:** `{"error": {"code", "message", "details"}}` for every 400, 401,
  403, 404, 405, 409 and 503.
- **SLA tracking:** due dates come from priority (24h / 72h / 7d), and the overdue
  filter is evaluated in the database.

## Workflow

| Action  | From              | To          | Who                                        |
|---------|-------------------|-------------|--------------------------------------------|
| triage  | submitted         | triaged     | property manager                           |
| assign  | triaged           | assigned    | property manager (+ `contractor_id`)       |
| start   | assigned          | in_progress | assigned contractor                        |
| resolve | in_progress       | resolved    | assigned contractor                        |
| close   | resolved          | closed      | current tenant of the unit, or manager     |
| reopen  | resolved          | triaged     | current tenant, within 14 days of resolve  |
| cancel  | submitted         | cancelled   | creating tenant, or manager                |
| cancel  | triaged, assigned | cancelled   | manager                                    |

## Stack

Python 3.13 · Django 5.2 LTS · Django REST Framework 3.18 · SimpleJWT ·
drf-spectacular (OpenAPI 3) · django-filter · PostgreSQL 16 (psycopg 3) · gunicorn +
WhiteNoise · pytest, factory_boy, ruff · GitHub Actions

## Local setup (Windows, native PostgreSQL, no Docker)

Prerequisites: Python 3.13 (via the `py` launcher) and PostgreSQL 16 running locally.

1. Create the database role and database once, as the Postgres superuser. Pick your
   own password:

   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost
   ```
   ```sql
   CREATE ROLE fixflow WITH LOGIN CREATEDB PASSWORD 'choose-a-password';
   CREATE DATABASE fixflow OWNER fixflow;
   ```
   `CREATEDB` lets pytest create its `test_fixflow` database.

2. Create the virtualenv and install dependencies (bare `python` may be the
   Microsoft Store alias, so use the venv's interpreter):

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

3. Configure the environment:

   ```powershell
   Copy-Item .env.example .env   # then edit .env: DATABASE_URL, SECRET_KEY, DEMO_PASSWORD
   ```

4. Migrate, seed and run:

   ```powershell
   .\.venv\Scripts\python.exe manage.py migrate
   .\.venv\Scripts\python.exe manage.py seed_demo
   .\.venv\Scripts\python.exe manage.py runserver
   ```

API docs: <http://127.0.0.1:8000/api/docs/> (Swagger UI), schema at `/api/schema/`.
Get a token from `POST /api/auth/token/` with a demo email (below) and your
`DEMO_PASSWORD`, then use **Authorize** in Swagger UI with `Bearer <access token>`.

### Environment variables

| Variable                 | Required          | Default                     | Purpose                                        |
|--------------------------|-------------------|-----------------------------|------------------------------------------------|
| `DATABASE_URL`           | yes               | none                        | `postgres://user:pass@host:5432/db`            |
| `SECRET_KEY`             | yes in prod       | insecure dev key (dev only) | Django signing key; prod refuses to start without it |
| `DEBUG`                  | no                | `True` dev, forced `False` prod | Debug mode                                 |
| `ALLOWED_HOSTS`          | yes in prod       | `localhost,127.0.0.1` (dev) | Comma-separated hostnames                      |
| `DJANGO_SETTINGS_MODULE` | no                | `config.settings.dev`       | `config.settings.{dev,test,prod}`              |
| `DEMO_PASSWORD`          | for `seed_demo`   | none                        | Password given to every demo user              |
| `JWT_ACCESS_MINUTES`     | no                | `15`                        | Access token lifetime                          |
| `JWT_REFRESH_DAYS`       | no                | `1`                         | Refresh token lifetime                         |
| `SECURE_SSL_REDIRECT`    | no (prod only)    | `True`                      | Set `False` behind plain HTTP (e.g. compose)   |

### Demo users (from `seed_demo`)

All of them use the password in `DEMO_PASSWORD`. The password is never printed.

| Email                          | Role       | Notes                       |
|--------------------------------|------------|-----------------------------|
| `manager@fixflow.example`      | manager    | manages both properties     |
| `plumber@fixflow.example`      | contractor |                             |
| `electrician@fixflow.example`  | contractor |                             |
| `alice@fixflow.example`        | tenant     | Maple Court 1A              |
| `bob@fixflow.example`          | tenant     | Maple Court 1A (roommate)   |
| `carol@fixflow.example`        | tenant     | Maple Court 1B              |
| `dave@fixflow.example`         | tenant     | Harbor View 101             |

The command is idempotent and seeds seven requests, one in every status, each with a
real audit trail.

## Running checks

```powershell
.\.venv\Scripts\python.exe -m pytest                          # tests + coverage (fails under 85%)
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe scripts\walkthrough.py             # needs runserver + seed_demo
```

`scripts/walkthrough.py` drives the full lifecycle over HTTP against any base URL.
It covers create, triage, assign, start, resolve, reopen, a second pass to closed, a
404 for a tenant from another unit, a 409 for an illegal move, and the audit log.

## API overview

All endpoints are under `/api/`.

| Endpoint                              | Methods          | Who                         |
|---------------------------------------|------------------|-----------------------------|
| `auth/token/`, `auth/token/refresh/`  | POST             | anyone                      |
| `me/`                                 | GET              | authenticated               |
| `health/`                             | GET              | anyone (503 if DB down)     |
| `properties/`, `properties/{id}/`     | GET, POST, PUT, PATCH | managers (own properties) |
| `properties/{id}/units/`              | GET, POST        | owning manager              |
| `units/{id}/tenancies/`               | GET, POST        | owning manager              |
| `tenancies/{id}/end/`                 | POST             | owning manager              |
| `requests/`                           | GET, POST        | scoped list; tenants create |
| `requests/{id}/`                      | GET, PATCH       | scoped; creator edits while submitted |
| `requests/{id}/transition/`           | POST             | per the workflow table      |
| `requests/{id}/events/`               | GET              | scoped, read-only audit log |
| `requests/{id}/comments/`             | GET, POST        | scoped; internal comments hidden from tenants |
| `requests/{id}/comments/{cid}/`       | GET              | scoped                      |

List filters on `requests/`: `status`, `priority`, `category`, `property`, `unit`,
`assigned_to`, `overdue=true|false`, `created_after`, `created_before`. Ordering:
`ordering=created_at|priority|sla_due_at` (prefix `-` to reverse). Pagination:
`page`, `page_size` (default 20, max 100).

## Layout

```
config/settings/{base,dev,test,prod}.py   env-driven settings
apps/core/          error handler, pagination, health, seed_demo
apps/accounts/      email-login User with roles, /me/
apps/properties/    Property, Unit, Tenancy + selectors/services
apps/maintenance/   requests, events, comments
  services/transitions.py   the state machine (only place status changes)
  selectors.py              visibility scoping
tests/              pytest suite (permission, transition and visibility matrices, concurrency)
scripts/walkthrough.py
```

## Docker (optional)

`Dockerfile` builds a slim, non-root gunicorn image with WhiteNoise for static files.
`docker-compose.yml` runs it with Postgres 16:

```powershell
$env:SECRET_KEY = "<long random string>"; docker compose up --build
```

Local development does not need Docker.
