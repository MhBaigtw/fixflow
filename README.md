# FixFlow

A backend-only REST API for rental maintenance requests. Tenants report problems,
property managers triage them and assign contractors, contractors do the work, and
tenants confirm the fix or reopen the request. Every status change and every edit is
audited.

**Live:** <https://fixflow-vzpj.onrender.com/api/docs/> (Swagger UI) ·
liveness: [`/api/health/live/`](https://fixflow-vzpj.onrender.com/api/health/live/) ·
readiness (checks the DB): [`/api/health/`](https://fixflow-vzpj.onrender.com/api/health/)

Hosting is an always-on Render Starter web service with Supabase Free Postgres
(session pooler). See [docs/deploy.md](docs/deploy.md), including why the database
moved from Neon.

**Status: all 4 phases complete.** CI runs the 385-test suite (99.66% coverage),
lint, migration and deploy checks, and builds and smoke-tests the Docker image.

## Try it: public demo logins

The demo logins are public by choice. The password is a throwaway used only for
this demo: **`demo-YVD2LQlb020I`**

| Email | Role | Notes |
|---|---|---|
| `manager@fixflow.example` | manager | manages both properties |
| `plumber@fixflow.example` | contractor | |
| `electrician@fixflow.example` | contractor | |
| `alice@fixflow.example` | tenant | Maple Court 1A |
| `bob@fixflow.example` | tenant | Maple Court 1A (Alice's roommate) |
| `carol@fixflow.example` | tenant | Maple Court 1B |
| `dave@fixflow.example` | tenant | Harbor View 101 |

1. In Swagger UI, `POST /api/auth/token/` with an email and the password above.
2. Click **Authorize** and paste `Bearer <access token>`.
3. Try `GET /api/requests/` as different users. Each role sees a different set, and a request outside your scope is a 404.

Demo data is shared by everyone and re-seeded on each deploy. Writes are throttled
(60/min per user, 10 logins/min per IP).

## What's interesting in the code

- **Strict state machine.** All status changes go through one transition table
  ([apps/maintenance/services/transitions.py](apps/maintenance/services/transitions.py)).
  - An illegal move from the current status gets `409 invalid_transition`.
  - A legal move by the wrong person gets `403`.
  - A test covers all 49 action × status pairs.
- **Row locking.** Each transition locks the row (`SELECT … FOR UPDATE OF self`),
  re-checks the status, then writes the change and its audit event in one transaction.
  Real-thread tests race conflicting transitions; all 8 fail if the lock is removed.
- **No cross-tenant leaks.** Every lookup goes through scoped selectors.
  - Out-of-scope objects are 404, never 403.
  - Roommates share visibility, and ending a tenancy removes it.
  - Filters and create don't reveal whether an id exists.
- **Complete audit trail.** `RequestEvent` is append-only (blocked at the model,
  queryset and admin levels). It records status changes plus field-level edit diffs
  (`{"priority": {"from": "routine", "to": "urgent"}, "sla_due_at": {…}}`).
- **Measured performance.** Queries per list page went from 23 to 3, and list p50 was about 3x faster on 10k
  requests. Indexes were added only where `EXPLAIN (ANALYZE)` showed a gain; one
  candidate was rejected. See [docs/benchmarks.md](docs/benchmarks.md).
- **Hardening.**
  - Scoped throttling, with counters in a shared database cache so all gunicorn workers count together.
  - The client IP is taken from a measured proxy depth, so forged `X-Forwarded-For` headers are ignored. **Known issue (2026-11-17):** after the Render plan change, the per-IP login limit stopped firing again; the proxy depth needs re-measuring (the per-user write limit still works). Originally verified live after finding and fixing a production bug where the login limit never fired ([details](docs/deploy.md#4-live-verification-done-2026-10-29)).
  - One error shape for every 4xx/5xx.

## Architecture

```mermaid
flowchart LR
    client["Client / Swagger UI"] -->|HTTPS| edge["Cloudflare + Render proxy<br/>(TLS, X-Forwarded-For)"]
    edge --> gunicorn["gunicorn<br/>2 workers"]
    gunicorn --> views["DRF views<br/>JWT auth · role permissions ·<br/>ScopedRateThrottle · error handler"]
    views --> selectors["selectors.py<br/>visibility scoping · filters · stats"]
    views --> services["services/<br/>transitions · edits · comments · tenancies"]
    services -->|"one transaction:<br/>SELECT … FOR UPDATE, UPDATE,<br/>INSERT RequestEvent"| db[("Supabase Postgres<br/>(session pooler :5432)")]
    selectors --> db
    views -->|throttle counters| cache[("django_cache table<br/>(DatabaseCache, same DB)")]
    probe["Render health check<br/>(every few seconds)"] -->|"/api/health/live/<br/>no DB query"| gunicorn
```

- **Health checks:** `/api/health/live/` (liveness) answers without touching the
  database, and it's what Render probes every few seconds. `/api/health/`
  (readiness) runs `SELECT 1` and returns 503 if the database is down. Probing
  readiness around the clock kept Neon's compute from ever idling and used up its
  free quota ([D28](docs/decisions.md)).

- **Views** stay thin: they validate input, call one service or selector, and serialize.
- **Services** hold every business rule; `transitions.py` is the only place a status changes.
- **Selectors** hold every read-side query and the per-role scoping.
- **Auth:** SimpleJWT bearer tokens (15 min access, 1 day refresh). There's no public sign-up.

## Measured results

From [docs/benchmarks.md](docs/benchmarks.md): 10,008 requests, in-process client
(no network), AMD Ryzen 5 5500U laptop, local Postgres 16. Compare p50; on this
machine p95 varies 40%+ between identical runs.

| Endpoint (20-row page) | Role | Queries before → after | p50 ms before → after |
|---|---|---|---|
| `GET /api/requests/` | manager (5,415 visible) | 23 → 3 | 67.8 → 21.0 |
| `GET /api/requests/` | tenant on busiest unit (259) | 23 → 3 | 56.4 → 20.9 |
| `GET /api/requests/` | contractor (3,761) | 23 → 3 | 67.5 → 18.2 |
| `GET /api/requests/{id}/` | each role | 3 → 2 | ~11–13 → ~9–10.5 |

At the database level, the manager list page query went from 22.99 ms to 0.41 ms
with `req_created_idx`
([index decisions](docs/benchmarks.md#index-decisions-explain-evidence)). The
remaining ~20 ms is Python (JWT, serialization, rendering), not the database.
[`tests/test_query_counts.py`](tests/test_query_counts.py) pins the query counts.

### Live performance (not the same as the table above)

On the deployed service (Render Starter + Supabase session pooler, both in Oregon),
measured 2026-11-17 from one client in Toronto through Cloudflare, 15 requests per
endpoint:

| Endpoint | Median | Min | Max |
|---|---:|---:|---:|
| `/api/health/live/` (no DB) | 253 ms | 139 ms | 459 ms |
| `/api/health/` (`SELECT 1`) | 156 ms | 143 ms | 335 ms |
| `/api/docs/` | 160 ms | 134 ms | 336 ms |

The full walkthrough (4 logins, create, 9 row-locked transitions, 404/409 checks,
audit log: 23 requests) took **6.6 s**, about 150–200 ms per transition. Most of
each number is the network round trip from Toronto to Oregon, so the liveness
check isn't really slower than readiness; the difference is noise. For comparison,
the earlier free tier + Neon setup had warm medians of 436–790 ms with outliers
up to 20 s ([HANDOFF.md](HANDOFF.md)). The laptop benchmark above remains the
controlled measurement of app + DB cost.

## Workflow

| Action | From | To | Who |
|---|---|---|---|
| triage | submitted | triaged | property manager (may set `priority`) |
| assign | triaged | assigned | property manager (+ `contractor_id`) |
| start | assigned | in_progress | assigned contractor |
| resolve | in_progress | resolved | assigned contractor |
| close | resolved | closed | current tenant of the unit, or manager |
| reopen | resolved | triaged | current tenant, within 14 days of resolve |
| cancel | submitted | cancelled | creating tenant, or manager |
| cancel | triaged, assigned | cancelled | manager |

The SLA is 24 h for emergency, 72 h for urgent and 7 days for routine, counted from creation.
The tenant's `requested_priority` is kept; the manager owns the working `priority`
until the request is resolved, and every change recomputes the SLA.

## API overview

All endpoints are under `/api/`.

| Endpoint | Methods | Who |
|---|---|---|
| `auth/token/`, `auth/token/refresh/` | POST | anyone (10/min per IP) |
| `me/` | GET | authenticated |
| `health/live/` | GET | anyone: liveness, no DB query (Render's health check) |
| `health/` | GET | anyone: readiness, `SELECT 1` (503 if DB down) |
| `stats/` | GET | managers: open by status, overdue, median hours to resolve per category |
| `properties/`, `properties/{id}/` | GET, POST, PUT, PATCH | managers (own properties) |
| `properties/{id}/units/` | GET, POST | owning manager |
| `units/{id}/tenancies/` | GET, POST | owning manager |
| `tenancies/{id}/end/` | POST | owning manager |
| `requests/` | GET, POST | scoped list; tenants create |
| `requests/{id}/` | GET, PATCH | scoped; creator edits while submitted, manager changes priority |
| `requests/{id}/transition/` | POST | per the workflow table |
| `requests/{id}/events/` | GET | scoped, read-only audit log |
| `requests/{id}/comments/`, `…/comments/{cid}/` | GET, POST | scoped; internal comments hidden from tenants |

List filters: `status`, `priority`, `category`, `property`, `unit`, `assigned_to`,
`overdue=true|false`, `created_after`, `created_before`. Ordering: `created_at`,
`priority` (by urgency), `sla_due_at`. Pagination: `page`, `page_size` (20, max 100).
Errors always look like `{"error": {"code", "message", "details"}}`.

## Stack

Python 3.13 · Django 5.2 LTS · Django REST Framework 3.18 · SimpleJWT ·
drf-spectacular · django-filter · PostgreSQL 16 (psycopg 3) · gunicorn + WhiteNoise ·
pytest, factory_boy, ruff · GitHub Actions · Render + Supabase.

## Local setup (Windows, native PostgreSQL, no Docker)

Prerequisites: Python 3.13 (`py` launcher) and PostgreSQL 16 running locally.

1. Create the role and database once, as the Postgres superuser (pick your own password):
   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost
   ```
   ```sql
   CREATE ROLE fixflow WITH LOGIN CREATEDB PASSWORD 'choose-a-password';
   CREATE DATABASE fixflow OWNER fixflow;
   ```
2. Create the virtualenv (bare `python` may be the Microsoft Store alias):
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```
3. Copy `.env.example` to `.env` and fill in `DATABASE_URL`, `SECRET_KEY` and `DEMO_PASSWORD`.
4. Migrate, create the cache table, seed and run:
   ```powershell
   .\.venv\Scripts\python.exe manage.py migrate
   .\.venv\Scripts\python.exe manage.py createcachetable
   .\.venv\Scripts\python.exe manage.py seed_demo
   .\.venv\Scripts\python.exe manage.py runserver
   ```
   Docs are then at <http://127.0.0.1:8000/api/docs/>.

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | yes | none | `postgres://user:pass@host:5432/db` |
| `SECRET_KEY` | yes in prod | insecure dev key (dev only) | prod refuses to start without it |
| `DEBUG` | no | `True` dev, forced `False` prod | |
| `ALLOWED_HOSTS` | prod (custom domains) | `localhost,127.0.0.1` (dev) | Render's hostname is added automatically |
| `CSRF_TRUSTED_ORIGINS` | no | none | `https://` + Render hostname added automatically |
| `DJANGO_SETTINGS_MODULE` | no | `config.settings.dev` | `config.settings.{dev,test,prod}` |
| `DEMO_PASSWORD` | for `seed_demo` | none | password for every demo user |
| `JWT_ACCESS_MINUTES` / `JWT_REFRESH_DAYS` | no | `15` / `1` | token lifetimes |
| `THROTTLE_LOGIN_RATE` / `THROTTLE_REFRESH_RATE` / `THROTTLE_WRITE_RATE` | no | `10/min` / `10/min` / `60/min` | DRF rate strings |
| `NUM_PROXIES` | prod | `0` | proxies appending to `X-Forwarded-For` (Render: `3`, measured; see [deploy.md](docs/deploy.md#4-live-verification-done-2026-10-29)) |
| `DB_SSLMODE` / `DB_CONN_MAX_AGE` | no (prod only) | `require` / `60` | DB TLS and connection reuse |
| `SECURE_SSL_REDIRECT` | no (prod only) | `True` | `False` only behind plain HTTP (compose) |

## Running checks, benchmarks and the walkthrough

```powershell
.\.venv\Scripts\python.exe -m pytest                          # tests + coverage (fails under 85%)
.\.venv\Scripts\python.exe -m ruff check .; .\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe scripts\walkthrough.py [BASE_URL]  # full lifecycle over HTTP (default: local)
.\.venv\Scripts\python.exe manage.py seed_bulk --seed 1       # 10k-request benchmark dataset (DEBUG only)
.\.venv\Scripts\python.exe scripts\benchmark.py --label MyRun --compare-to Optimized
.\.venv\Scripts\python.exe scripts\explain_queries.py         # EXPLAIN (ANALYZE, BUFFERS) of list queries
```

The walkthrough's output against the live service is recorded in
[HANDOFF.md](HANDOFF.md).

## More

- [SPEC.md](SPEC.md): what was built, phase by phase.
- [docs/decisions.md](docs/decisions.md): every non-obvious choice and its reason.
- [docs/benchmarks.md](docs/benchmarks.md): measurements and index evidence.
- [docs/deploy.md](docs/deploy.md): provider terms (Render Starter, Supabase Free), deploy configuration, setup steps and the Neon → Supabase move.
- [docs/resume-bullets.md](docs/resume-bullets.md): claims, each linked to its evidence.
- Docker (optional): `Dockerfile` (slim, non-root, gunicorn) and `docker-compose.yml`. CI builds the image and smoke-tests it against Postgres.
