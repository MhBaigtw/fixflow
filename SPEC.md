# FixFlow: Product Spec

A backend-only REST API for rental maintenance requests. Tenants report problems,
property managers triage and assign contractors, contractors do the work, and
tenants confirm or reopen. Every status change is audited.

Engineering focus:

- a strict workflow state machine
- object-level permissions (no cross-tenant data leaks)
- row locking for concurrent updates
- measured query performance

## Stack (locked)

Django 5.2 LTS, Django REST Framework, djangorestframework-simplejwt,
drf-spectacular, django-filter, django-environ, psycopg 3, gunicorn, whitenoise.
Dev: pytest, pytest-django, pytest-cov, factory_boy, ruff. PostgreSQL 16.
Local dev runs on native Postgres. Docker is optional.

## Conventions

- Business rules live in services (`apps/<app>/services.py` or a `services/` package). Views and serializers stay thin.
- Read-side query logic lives in `selectors.py`.
- Object scoping lives in querysets plus permission classes, never ad hoc per view.
- Out-of-scope objects return 404, not 403, so existence is never leaked. A wrong role on an allowed resource type returns 403.
- One error shape everywhere: `{"error": {"code": "<machine_code>", "message": "<human message>", "details": {...}}}`.
- Time is stored in UTC. Durations are computed in the database where practical.
- Every business rule has a test. No float money.
- No speculative abstractions, base-class hierarchies or plugin systems.

---

## Phase 1: Foundation

- Settings package `config/settings/{base,dev,test,prod}.py`. Secrets and DB come from env (`DATABASE_URL`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DJANGO_SETTINGS_MODULE`, `DEMO_PASSWORD`, JWT lifetimes). `.env.example` is committed and `.env` is gitignored. `prod.py` refuses to start without `SECRET_KEY`, forces `DEBUG=False`, and sets secure cookies and HSTS.
- Apps under `apps/`: `core`, `accounts`, `properties`.
- Custom user before the first migration: `accounts.User` extends `AbstractUser`. No username; a unique email is the `USERNAME_FIELD`. `role` is a TextChoices of tenant, manager or contractor. Custom manager.
- Models:
  - **Property**: name, address, manager (FK User, role=manager), created_at.
  - **Unit**: property FK, label; `UniqueConstraint(property, label)`.
  - **Tenancy**: unit FK, tenant (FK User, role=tenant), start_date, end_date (nullable). Several active tenants per unit are allowed (roommates). `UniqueConstraint(unit, tenant) WHERE end_date IS NULL`.
- Endpoints under `/api/`:
  - `POST auth/token/`, `auth/token/refresh/` (SimpleJWT; access token 15 min, refresh token 1 day, env-configurable)
  - `GET me/`
  - `properties/`: managers list, create, retrieve and update their own
  - `properties/{id}/units/`: list and create (owning manager)
  - `units/{id}/tenancies/`: `POST` attaches an existing tenant-role user by email (owning manager). Rejects a non-tenant role and a duplicate active tenancy with 400.
  - `tenancies/{id}/end/`: `POST` (owning manager)
  - `health/`: unauthenticated; 200 with a DB check, 503 if the DB is down
  - `schema/` (OpenAPI 3) and `docs/` (Swagger UI)
  - No public sign-up.
- Custom exception handler for the error shape (validation, 401, 403, 404, 405, 409).
- Page-number pagination: default page size 20, max 100.
- Admin registration for all models.
- `seed_demo` management command: idempotent. Creates 1 manager, 2 contractors, 4 tenants, and 2 properties with units and tenancies, plus maintenance requests in varied states. The password comes from `DEMO_PASSWORD` and is never hard-coded or printed.
- Dockerfile (Python slim, gunicorn, non-root, whitenoise) and docker-compose.yml (web + postgres).
- CI on push and pull_request with a Postgres 16 service: install, `ruff check`, `ruff format --check`, `makemigrations --check --dry-run`, `check --deploy` with prod settings, pytest with coverage that fails under 85%.

## Phase 2: Core workflow

New app: `apps/maintenance`.

### Models

- **MaintenanceRequest**: unit FK, created_by (tenant), title, description, category (plumbing, electrical, heating, appliance, pest, structural, other), priority (emergency, urgent, routine), status (submitted, triaged, assigned, in_progress, resolved, closed, cancelled), assigned_to (FK contractor, nullable), sla_due_at, resolved_at, closed_at, created_at, updated_at.
- **RequestEvent** (append-only audit): request FK, actor FK, from_status, to_status, note, created_at. Events cannot be updated or deleted through the API or the service layer.
- **Comment**: request FK, author FK, body, is_internal, created_at.

### SLA

`sla_due_at = created_at + target(priority)`. Targets: emergency 24h, urgent 72h, routine 7 days; they live in settings. A request is overdue when its status is not resolved, closed or cancelled and now > `sla_due_at`.

### State machine

There is a single transition table in `apps/maintenance/services/transitions.py`, and it is the only place where status changes.

| Action   | From                | To          | Who                                          |
|----------|---------------------|-------------|----------------------------------------------|
| triage   | submitted           | triaged     | manager of the property                      |
| assign   | triaged             | assigned    | manager; requires `contractor_id` of a contractor |
| start    | assigned            | in_progress | the assigned contractor                      |
| resolve  | in_progress         | resolved    | the assigned contractor; sets `resolved_at`  |
| close    | resolved            | closed      | a current tenant of the unit, or the manager; sets `closed_at` |
| reopen   | resolved            | triaged     | a current tenant of the unit, within 14 days of `resolved_at` (configurable); clears `resolved_at` and `assigned_to` |
| cancel   | submitted           | cancelled   | the creating tenant or the manager           |
| cancel   | triaged, assigned   | cancelled   | manager only                                 |

Any other move returns 409 with code `invalid_transition`. An allowed move by the wrong actor returns 403 if the actor can see the request, or 404 if they cannot.

Each transition runs in `transaction.atomic`, locks the row with `select_for_update`, re-checks the current status after locking, then updates the request and writes the RequestEvent in the same transaction. Creation also writes an initial event (null -> submitted).

### Visibility

- tenant: requests on units where they hold an active tenancy (roommates share visibility)
- manager: requests on properties they manage
- contractor: requests assigned to them
- Internal comments are invisible to tenants (excluded from list and detail), and tenants cannot create them.

### Endpoints

- `requests/`: `GET` list (scoped); `POST` create (only a tenant with an active tenancy on the given unit)
- `requests/{id}/`: `GET` detail; `PATCH` title, description, category or priority, only by the creating tenant while status=submitted (a priority change recomputes `sla_due_at`)
- `requests/{id}/transition/`: `POST {"action", "note", "contractor_id"?}`
- `requests/{id}/events/`: `GET` the audit log
- `requests/{id}/comments/`: `GET`, `POST`
- List filters: status, priority, category, property, unit, assigned_to, overdue=true, created_after, created_before. Ordering: created_at, priority, sla_due_at. Paginated.

Phase 2 querysets are written plainly on purpose: no `select_related`, `prefetch_related`, performance annotations or custom indexes. Phase 3 benchmarks this baseline first.

## Phase 3: Measure and harden (not started)

- `seed_bulk` command for 10,000 requests with realistic distributions.
- Benchmark script recording queries per request and p50/p95 latency for the list and detail endpoints per role, saved to `docs/benchmarks.md` with hardware, commit hash and command.
- Optimize (`select_related`/`prefetch_related`, composite indexes), then re-measure.
- Query-count regression tests.
- DRF scoped throttling on login and writes.
- Manager stats endpoint: open requests by status, overdue count, median time to resolve by category.

## Phase 4: Ship (not started)

- Deploy to Render (web) + Neon (Postgres) after checking their current free-tier terms. Humayun creates the accounts and enters the secrets himself.
- Demo walkthrough script that runs against any base URL.
- Full README with a Mermaid architecture diagram.
- Resume bullets in `docs/resume-bullets.md`, each linked to its measured evidence.
