# HANDOFF: FixFlow Checkpoint 1 (Phases 1-2)

## Status

**Done locally; not yet pushed.** Phases 1 and 2 are built and every local check passes.
Two items are still open:

- Creating the GitHub repo and pushing it (waiting for Humayun's approval).
- Confirming the CI run, which can only happen after the push.

## Versions installed

- Python 3.13.3 (`py` launcher); CI and the Dockerfile use 3.13 to match
- Django 5.2.18, djangorestframework 3.18.3, djangorestframework_simplejwt 5.5.1
- drf-spectacular 0.30.0, django-filter 26.2, django-environ 0.14.0, psycopg 3.3.6
- gunicorn 26.2.0, whitenoise 6.12.0
- pytest 9.1.1, pytest-django 4.14.0, pytest-cov 7.1.0, factory_boy 3.3.3, ruff 0.17.3
- PostgreSQL 16 (native Windows service `postgresql-x64-16`)

## What was built

**Phase 1:**
- Settings: an env-driven package. `prod.py` refuses to start without `SECRET_KEY` (tested), and forces `DEBUG=False`, secure cookies, HSTS and the SSL redirect.
- Accounts: a custom email-login `User` with roles, created before the first migration. JWT obtain/refresh endpoints and `/me/`.
- Properties: properties, units and tenancies, with partial-unique active tenancies and manager-scoped permissions.
- Platform:
  - One error shape for 400, 401, 403, 404, 405, 409, 503 and 500. Django's own 404/500 pages also return JSON.
  - Health check with a database probe.
  - OpenAPI schema and Swagger UI.
  - Pagination (20 per page, max 100).
  - Admin for every model.
- `seed_demo`: idempotent; password from `DEMO_PASSWORD`, never printed.
- Docker: Dockerfile and docker-compose.
- CI on GitHub Actions with a Postgres 16 service.

**Phase 2:**
- The `maintenance` app: requests, an append-only `RequestEvent` audit log, and comments.
- A single transition table with row-locked, re-checked transitions.
- An initial audit event when a request is created.
- SLA due dates per priority, set in settings.
- Visibility by role: tenants see their unit (roommates included), managers see their properties, contractors see what's assigned to them.
- Internal comments hidden from tenants.
- PATCH by the creating tenant only, only while the request is submitted.
- List filters (including `overdue`) and ordering, with priority ordered by urgency.
- `scripts/walkthrough.py`.

## Key files and what each does

| File | What it does |
|---|---|
| `config/settings/{base,dev,test,prod}.py` | Settings; secrets and DB from env; prod hardening |
| `apps/core/exceptions.py` | The error-shape exception handler; `Conflict` / `InvalidTransition` (409) |
| `apps/core/views.py` | Health check (200/503); JSON 404/500 handlers |
| `apps/core/management/commands/seed_demo.py` | Idempotent demo data, driven through the real services |
| `apps/accounts/models.py` | `User` (email login, role), `UserManager` |
| `apps/accounts/permissions.py` | `IsManager`, `IsTenant` role checks (403) |
| `apps/properties/selectors.py` | Manager scoping, active tenancies (out-of-scope rows are 404s) |
| `apps/properties/services.py` | Attach a tenant by email; end a tenancy |
| `apps/maintenance/services/transitions.py` | **The state machine.** The table, locking, side effects, event writing |
| `apps/maintenance/services/requests.py` | Create (with the initial event), edit rules, SLA calculation |
| `apps/maintenance/services/comments.py` | Comment creation; tenants can't post internal comments |
| `apps/maintenance/selectors.py` | Request visibility per role, priority rank, the overdue condition |
| `apps/maintenance/filters.py` | List filters and ordering |
| `apps/maintenance/views.py` | Thin viewset; every lookup goes through the scoped queryset |
| `tests/` | 297 tests: permission, transition and visibility matrices, concurrency, SLA, seed, admin |
| `scripts/walkthrough.py` | Stdlib-only HTTP walkthrough against any base URL |
| `.github/workflows/ci.yml` | Lint, format, migrations, `check --deploy`, schema validation, pytest ≥85%, Docker build |

## Decisions beyond this prompt, with reasons

All 17 are written up in [docs/decisions.md](docs/decisions.md). The most important ones:

1. **Database setup (D1).** I created the `fixflow` role and database myself through your existing pgpass superuser entry. I never saw, asked for or printed the superuser password. The new role's password was generated randomly and exists only in `.env`.
2. **Check order (D4).** Out of scope → 404. Then unknown action → 400. Then illegal move → 409. Then wrong actor → 403.
3. **Extra 409 codes (D5):** `reopen_window_expired`, `request_not_editable`, `tenancy_already_ended`.
4. **Two extra read endpoints (D7):** a tenancy list for each unit, and comment detail.
5. **Audit log protection (D10).** Events are append-only at the model, queryset and admin levels. Requests can't be deleted (`PROTECT`). The admin can't add requests or change workflow fields.
6. **No existence leaks (D11).** Filters and create never reveal whether an out-of-scope id exists.
7. **Strict input (D12).** `?overdue=` accepts only true/false. PATCH with non-editable fields returns 400. A test caught the original lenient `BooleanFilter`.
8. **Concurrency tests (D16)** were confirmed to fail when `select_for_update()` is removed. The transition matrix was confirmed to fail when one rule is loosened.

## Verification output

### pytest (full suite, coverage gate 85%)

```
Name                            Stmts   Miss  Cover   Missing
-------------------------------------------------------------
apps\maintenance\selectors.py      23      1    96%   27
-------------------------------------------------------------
TOTAL                             842      1    99%

41 files skipped due to complete coverage.
Required test coverage of 85% reached. Total coverage: 99.88%
297 passed in 35.28s
```

The concurrency tests were run five extra times in a row: `4 passed` each time.

With `select_for_update()` removed as a deliberate mutation, all 4 concurrency tests failed:

```
FAILED tests/test_concurrency.py::test_triage_vs_cancel_race[False]
FAILED tests/test_concurrency.py::test_triage_vs_cancel_race[True]
FAILED tests/test_concurrency.py::test_double_submit_of_same_transition
FAILED tests/test_concurrency.py::test_second_transaction_waits_for_lock
4 failed
```

### makemigrations --check --dry-run

```
No changes detected
exit=0
```

### ruff

```
ruff check .           -> All checks passed!
ruff format --check .  -> 65 files already formatted
```

### check --deploy (prod settings, dummy env, `--fail-level WARNING`)

```
System check identified no issues (0 silenced).
exit=0
```

### OpenAPI schema (`spectacular --validate --fail-on-warn`)

```
exit=0
```

On the live dev server, `/api/docs/` and `/api/schema/` both returned 200.

### HTTP walkthrough (`scripts/walkthrough.py` against `runserver` on 127.0.0.1:8000, after `seed_demo`)

Tokens and passwords are redacted by the script.

```
[ok ] health check                    GET  /api/health/ -> 200  {"status": "ok", "database": "ok"}
[ok ] login alice@fixflow.example     POST /api/auth/token/ -> 200  {"refresh": "<redacted>", "access": "<redacted>"}
[ok ] login manager / plumber / carol POST /api/auth/token/ -> 200 (x3, redacted)
[ok ] plumber /me/                    GET  /api/me/ -> 200  {"id": 2, "email": "plumber@fixflow.example", ..., "role": "contractor"}

--- Tenant creates a request
[ok ] alice creates request           POST /api/requests/ -> 201
      {"id": 8, "unit": 1, "property": 1, "created_by": 4, "title": "Shower has no hot water", "category": "plumbing",
       "priority": "urgent", "status": "submitted", "assigned_to": null, "sla_due_at": "2026-10-30T07:27:40.245886Z",
       "is_overdue": false, "created_at": "2026-10-27T07:27:40.245886Z", ...}

--- Manager triages and assigns; contractor starts and resolves
[ok ] manager triages          -> 200  {"status": "triaged",     "assigned_to": null}
[ok ] manager assigns plumber  -> 200  {"status": "assigned",    "assigned_to": 2}
[ok ] plumber starts           -> 200  {"status": "in_progress", "assigned_to": 2}
[ok ] plumber resolves         -> 200  {"status": "resolved",    "resolved_at": "2026-10-27T07:27:40.786969Z"}

--- Tenant reopens (within the 14-day window)
[ok ] alice reopens            -> 200  {"status": "triaged", "assigned_to": null, "resolved_at": null}

--- Second pass through to closed
[ok ] manager re-assigns       -> 200  {"status": "assigned"}
[ok ] plumber starts again     -> 200  {"status": "in_progress"}
[ok ] plumber resolves         -> 200  {"status": "resolved"}
[ok ] alice closes             -> 200  {"status": "closed", "closed_at": "2026-10-27T07:27:41.594982Z"}

--- Isolation and invalid moves
[ok ] carol (other unit) GET detail       GET  /api/requests/8/ -> 404
      {"error": {"code": "not_found", "message": "Not found.", "details": {}}}
[ok ] carol (other unit) tries to reopen  POST /api/requests/8/transition/ -> 404
      {"error": {"code": "not_found", "message": "Not found.", "details": {}}}
[ok ] manager triages a closed request    POST /api/requests/8/transition/ -> 409
      {"error": {"code": "invalid_transition", "message": "Cannot 'triage' a request whose status is 'closed'.",
                 "details": {"action": "triage", "current_status": "closed"}}}

All steps returned the expected status codes.
```

### Audit log for the walkthrough request (#8)

Actors: 4 = alice (tenant), 1 = manager, 2 = plumber.

```
2026-10-27T07:27:40.252071Z  actor=4   None         -> submitted
2026-10-27T07:27:40.406775Z  actor=1   submitted    -> triaged      Checking boiler first
2026-10-27T07:27:40.547153Z  actor=1   triaged      -> assigned
2026-10-27T07:27:40.661330Z  actor=2   assigned     -> in_progress
2026-10-27T07:27:40.789291Z  actor=2   in_progress  -> resolved     Reset boiler pressure
2026-10-27T07:27:41.043473Z  actor=4   resolved     -> triaged      Cold again by evening
2026-10-27T07:27:41.200967Z  actor=1   triaged      -> assigned
2026-10-27T07:27:41.319742Z  actor=2   assigned     -> in_progress
2026-10-27T07:27:41.458392Z  actor=2   in_progress  -> resolved     Replaced faulty thermostat
2026-10-27T07:27:41.597245Z  actor=4   resolved     -> closed
```

### seed_demo run twice against the dev DB

```
Demo data ready (newly created: 7 users, 2 properties, 5 units, 4 tenancies, 7 requests).
Demo data ready (newly created: 0 users, 0 properties, 0 units, 0 tenancies, 0 requests).
```

### git log --oneline

```
(regenerated at push time; see the end of this file)
```

### CI run result

Not run yet: the repo hasn't been pushed. See the end of this file.

## Known gaps, risks, surprises

- **Docker was not run locally** (by instruction: Docker is unreliable on this machine). The CI `docker` job builds the image, but `docker compose up` itself hasn't been exercised anywhere.
- **The CI workflow hasn't run yet.** It was checked locally step by step with the same commands; a Linux runner may still surface something.
- **Login email is case-sensitive.** Emails are stored lowercase, but `Alice@fixflow.example` won't log in (D14).
- **Phase 2 is intentionally N+1.** Every list row triggers extra queries, for example the `property` field. This is the baseline Phase 3 will measure (D2).
- **No throttling yet**, including on login (Phase 3).
- **Swagger UI loads its JS/CSS from a CDN** (drf-spectacular's default), so `/api/docs/` doesn't render offline. `/api/schema/` works offline.
- **Future-dated tenancies count as active** (D8).
- **Edits aren't audited.** Changing title, description or priority while submitted writes no `RequestEvent`, because events record status changes only. Priority edits do recompute the SLA.
- **`createsuperuser` gives the role `manager` by default** (it prompts for it, since `role` is in `REQUIRED_FIELDS`). Admin-only staff would need a separate role if wanted later.
- **One uncovered line:** `apps/maintenance/selectors.py:27`, the "unknown role → no requests" fallback, which can't happen with valid roles.
- **Surprise 1:** drf-spectacular's warnings made `check --deploy --fail-level WARNING` fail until the views short-circuited `get_queryset` during schema generation. Fixed.
- **Surprise 2:** django-filter's `BooleanFilter` silently ignores invalid values. Replaced with a strict choice filter.

## Actions Humayun must take

1. Approve creating the public repo `MhBaigtw/fixflow` and pushing `main`. `gh` is already authenticated as MhBaigtw, so I can run it myself:
   ```powershell
   gh repo create MhBaigtw/fixflow --public --source . --remote origin --push
   ```
2. Nothing else is needed for local use. `.env` already exists, with a random `fixflow` DB password, `SECRET_KEY` and `DEMO_PASSWORD`. To see the demo password locally:
   ```powershell
   Select-String DEMO_PASSWORD .env
   ```

## Repo URL and latest commit hash

Pending push (see below).
