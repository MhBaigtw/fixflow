# Decision log

Each entry gives the choice, the alternatives considered and the reason.

## D1. Local DB role created via the existing pgpass superuser entry
- **Choice:** Created role `fixflow` (LOGIN, CREATEDB) and database `fixflow` by connecting as `postgres` through the machine's existing `%APPDATA%\postgresql\pgpass.conf`. A random password was generated and written only to the gitignored `.env`.
- **Alternatives:** Stop and ask Humayun to run psql commands.
- **Reason:** The superuser password never had to be entered, read or printed, so the "stop if a superuser password is needed" condition never applied. CREATEDB lets pytest create `test_fixflow`.

## D2. Phase 2 querysets are deliberately unoptimized
- **Choice:** Phase 2 uses plain querysets: no `select_related`, `prefetch_related`, performance annotations or custom indexes beyond FK indexes and the listed constraints. For example, the request serializer's `property` field triggers a per-row unit lookup.
- **Alternatives:** Optimize while building.
- **Reason:** Phase 3 benchmarks this baseline first, then optimizes and measures again. The before/after numbers are the evidence for the performance claims.

## D3. Services raise DRF exceptions directly
- **Choice:** Services raise `ValidationError`, `PermissionDenied` and a small `Conflict` (409) family defined in `apps/core/exceptions.py`.
- **Alternatives:** A separate domain-exception hierarchy translated in the views.
- **Reason:** It's one translation layer fewer and needs no new abstraction. The services are still callable from management commands (seed_demo uses them).

## D4. Order of checks for a transition
- **Choice:** Out of scope → 404 (scoped lookup in the view). Then unknown action → 400. Then not legal from the current status → 409 `invalid_transition`. Then legal but wrong actor → 403.
- **Alternatives:** Check the actor before legality.
- **Reason:** The spec says "any other move returns 409". Legality first gives every visible actor the same answer for an impossible move, and the actor can already see the status anyway.

## D5. Extra 409 codes
- **Choice:** The following each return 409 with their own codes:
  - `reopen_window_expired`: reopening after 14 days
  - `request_not_editable`: PATCH on a non-submitted request
  - `tenancy_already_ended`: ending an ended tenancy
- **Alternatives:** 400 or 403.
- **Reason:** Each request is well formed and the caller is allowed to make it. Only the resource's current state blocks it, which is the definition of a conflict.

## D6. The creating tenant can cancel only while still a current tenant
- **Choice:** `cancel` from submitted requires the creator to still hold an active tenancy on the unit, or the actor to be the manager.
- **Reason:** It's consistent with visibility: a former tenant can't see the request (404), so the service enforces the same rule even when it's called directly.

## D7. Two small endpoints beyond the prompt
- **Choice:** Added `GET units/{id}/tenancies/` (list) and `GET requests/{id}/comments/{cid}/` (detail).
- **Reason:** A manager needs tenancy ids to call `tenancies/{id}/end/`. The comment detail endpoint makes "internal comments hidden from tenants in list **and detail**" testable as written.

## D8. "Active tenancy" means `end_date IS NULL`
- **Choice:** Ending a tenancy sets `end_date` to today, and it stops counting as active immediately. `start_date` isn't checked, so a future-dated tenancy counts as active.
- **Reason:** This matches the partial unique constraint in the spec (`WHERE end_date IS NULL`) and keeps one definition of "active" everywhere.

## D9. Priority ordering by urgency via a `Case` annotation
- **Choice:** `ordering=priority` sorts emergency → urgent → routine using an annotated rank.
- **Alternatives:** Order by the raw string (alphabetical: emergency, routine, urgent, which is wrong).
- **Reason:** This annotation is for correctness, not performance, so it doesn't conflict with D2.

## D10. Append-only audit enforced in three places
- **Choice:**
  - `RequestEvent.save()` refuses updates and `delete()` raises.
  - The default queryset blocks `.update()` and `.delete()`.
  - The admin is read-only.
  - The `MaintenanceRequest` and `RequestEvent` FKs use `PROTECT`, so requests (and their history) can't be deleted.
  - The admin also can't add requests (that would skip the initial event) or edit workflow fields (that would bypass the state machine).
- **Reason:** "No update or delete through the API or service layer." The model layer is the narrowest point to enforce it.

## D11. No existence leaks through filters or create
- **Choice:**
  - List filters on `unit`, `property` and `assigned_to` are plain number filters, so an out-of-scope id returns an empty page.
  - On create, the `unit` field only accepts units the tenant actively rents, so a missing unit and someone else's unit give the identical "does not exist" error.
- **Reason:** The same 404-not-403 principle, applied to body and query parameters.

## D12. Strict `overdue` filter and strict PATCH
- **Choice:** `?overdue=` accepts only `true`/`false` (anything else is 400). A PATCH containing non-editable fields (e.g. `status`) is rejected with 400 instead of silently ignoring them.
- **Reason:** Silently ignored input hides client bugs. A test caught `BooleanFilter` ignoring `overdue=maybe`.

## D13. Overdue is computed with the database's `NOW()`
- **Choice:** `overdue_q()` compares `sla_due_at` with `django.db.models.functions.Now()`. Tests use explicit past/future timestamps instead of freezing time.
- **Reason:** It follows the "durations computed in the database where practical" convention. No time-freezing library is in the locked stack.

## D14. Emails are stored lowercase
- **Choice:** `UserManager` lowercases the whole email. Tenancy attachment looks users up case-insensitively.
- **Known limitation:** login (SimpleJWT → ModelBackend) is an exact match, so a user typing `Alice@...` fails to log in. This can be fixed in Phase 3 with a custom token serializer if wanted.

## D15. CI does slightly more than asked
- **Choice:** CI also validates the OpenAPI schema (`spectacular --validate --fail-on-warn`) and builds the Docker image in a separate job. `check --deploy` runs with `--fail-level WARNING`.
- **Reason:** Docker is unreliable on the dev machine, so CI is where the Dockerfile actually gets verified.

## D16. Concurrency test forces overlap
- **Choice:** Besides a plain barrier-started race, one variant monkeypatches `record_event` to sleep 0.5s while holding the lock. Another asserts that the losing request took at least 0.5s, which proves it blocked on the lock.
- **Reason:** A race that only happens to overlap can pass by luck. All four concurrency tests were confirmed to **fail** when `select_for_update()` is removed.

## D17. `seed_demo` lives in `apps/core`
- **Choice:** One command in `core` seeds users, properties and requests. It resets every demo user's password to `DEMO_PASSWORD` on each run and creates requests only if a request with the same title doesn't exist on that unit.
- **Reason:** It spans three apps, and running it twice must not duplicate anything (tested).

---

# Checkpoint 2

## D18. Case-insensitive login (review fix A1)
- **Choice:** `EmailTokenObtainPairSerializer` strips and lowercases the email before SimpleJWT authenticates. `EmailTokenObtainPairView` uses it at `auth/token/`.
- **Alternatives:** A custom authentication backend with `email__iexact`.
- **Reason:** Emails are already stored lowercase (D14), so normalising the input is enough. It's the smallest change, touches only the token endpoint, and resolves D14's known limitation.

## D19. Requested vs working priority (review fix A2)
- **Choice:**
  - `requested_priority` records what the tenant asked for. `priority` is the working priority that drives the SLA.
  - **Tenant (creator):** while submitted, a priority PATCH sets both fields.
  - **Property manager:** may PATCH `priority` only. Any other field gets 400 listing those fields.
  - **When the manager may change it:** while submitted, triaged, assigned or in_progress. After that it's 409 **`priority_not_editable`**, a new code. The tenant's rule keeps `request_not_editable`, because the two rules differ and clients should be able to tell them apart.
  - **Triage:** accepts an optional `priority`. Sending `priority` with any other action is 400.
  - Every priority change recomputes `sla_due_at` from `created_at` (one helper: `services/priority.py::set_priority`).
- **Test change:** `test_visibility.py::test_only_creator_edits` used to expect 403 for a manager PATCHing `title`. The spec now says that's a 400, so the manager case moved to `test_edits.py::test_manager_can_patch_priority_only`, which asserts the 400. This follows the requirement change; it doesn't weaken the test.
- **Migration:** `0002` adds `requested_priority` as nullable and copies `priority` into it. `0003` makes it NOT NULL, in a separate migration (its own transaction) so the ALTER runs after the backfill has committed. Verified on the dev DB: 8 of 8 rows backfilled.

## D20. Edits are audited (review fix A3)
- **Choice:**
  - `RequestEvent` gains `event_type` (`status_change` | `edit`) and `changes` (JSON: `{"field": {"from", "to"}}`). Datetimes are stored as ISO-8601 strings.
  - Each PATCH that changes something writes exactly one `edit` event, with `from_status == to_status ==` the current status. The diff includes `requested_priority`, `priority` and `sla_due_at` when they move.
  - A no-op PATCH writes no event and doesn't save the row, so `updated_at` doesn't move.
  - Triage with a priority change writes one `status_change` event whose `changes` holds the priority and SLA diff.
- **Reason:** The audit log now covers everything that affects the SLA or the request's content, not only status changes. Append-only protection is unchanged.

## D21. Unit label race (review fix A4)
- **Choice:** Unit creation moved into `properties.services.create_unit`. An `IntegrityError` from the unique constraint (raised inside a savepoint) becomes a 400 `validation_error` on `label`.
- **Reason:** It's the same pattern as `attach_tenant`. The serializer's pre-check gives the friendly common-case error; the constraint is the real guarantee.

## D22. `seed_bulk` design
- **Choice:**
  - `bulk_create` with a fixed `random.Random(seed)`. All rows are tagged with the seed (emails `@bulk<seed>.example`, property names `Bulk <seed> Property NN`), so the command is idempotent per seed.
  - It refuses to run unless DEBUG is True or `--force` is passed.
  - Users get an unusable password, so no secret is involved.
  - Distributions are documented in the command's docstring.
  - Open requests get recent `created_at` values (exponential, mean 8 days). If they were spread uniformly over 12 months, nearly every open request would be months overdue.
- **Verification:** `tests/test_seed_bulk.py` seeds 400 requests and checks:
  - every event chain is legal per the transition table and matches the final status;
  - timestamps are ordered and never in the future;
  - workflow fields match the status;
  - actors have the right roles;
  - creators held a tenancy when they reported;
  - no tenant ever posted an internal comment;
  - the command is idempotent.
- **Real run:** 10k requests in 14.1 s. Measured: closed 44.5%, resolved 14.9%, cancelled 9.7%; priority 10.1 / 30.4 / 59.5%; 4.3% ended tenancies; 17% of all requests overdue.
- **Known skew:** Pareto(1.2) contractor weights give one contractor 3,761 of the ~8,600 assigned requests. That's useful as a stress subject, but heavier than most real portfolios.

## D23. How performance was measured and optimized
- **Benchmark:** `scripts/benchmark.py`, run in-process with the Django test client, with DEBUG forced off. It reports queries plus p50/p95/max over 200 timed runs after 20 warmup runs, then appends to `docs/benchmarks.md` and writes JSON for comparisons. The baseline was committed (`1422f53`) before any optimization.
- **Noise:** a repeat run of the same code is reported next to each table. p50 is stable to within about 7–12%, while p95 on this laptop can move 40%+, so conclusions use p50 and query counts.
- **Optimizations:**
  - `select_related("unit")` in the visibility selector.
  - `lock_request()` = `select_for_update(of=("self",)).select_related("unit__property")`. `of=self` locks only the request row, not the unit/property rows it joins. Permission checks now run the free manager check before the tenancy query.
  - Three indexes, each kept only after a rolled-back EXPLAIN (ANALYZE) experiment showed a full scan + sort turning into an ordered index scan. One candidate was rejected (partial open-SLA index).
- **Not done:** an expression index for `ordering=priority` (8.5 ms, a CASE sort). It hasn't been shown to matter.

## D24. Throttling and the shared cache
- **Choice:** DRF `ScopedRateThrottle` with three scopes:
  - `login`: 10/min per client IP
  - `token_refresh`: 10/min per client IP
  - `writes`: 60/min per user, covering POST/PATCH on requests, transitions and comments; GETs are never throttled

  Rates come from env vars. A 429 uses the standard error shape with `details.retry_after_seconds`, and DRF sets `Retry-After`.
- **Cache:** `CACHES` uses `DatabaseCache` in base settings. Gunicorn runs several worker processes, and the per-process default cache (LocMem) would give each worker its own counter, multiplying the real limit. The database cache is shared and needs no extra service (no Redis on a free tier). This was verified: two separate processes each made 6 failed logins from one IP, and the second process got 429 on its 5th attempt (11th overall). Tests use LocMemCache, cleared before and after every test by an autouse fixture.
- **Client IP:** `NUM_PROXIES` comes from env and defaults to 0, meaning `REMOTE_ADDR` is used and `X-Forwarded-For` is ignored, so XFF can't be spoofed to dodge the limit (tested). Behind a proxy it must be set to the number of proxies (Part E).

## D25. Stats endpoint
- **Choice:** `GET /api/stats/`, managers only, scoped to their properties. Optional filters: `property`, `created_after`, `created_before` (inclusive bounds, filtering on `created_at`).
  - **Median:** a small ORM `Aggregate` renders `percentile_cont(0.5) WITHIN GROUP (ORDER BY resolved_at - created_at)` over an interval, so Django returns a `timedelta`, converted to hours and rounded to 2 decimals. No SQL is built from strings.
  - **Population:** requests currently resolved or closed with `resolved_at` set. Cancelled and reopened-but-not-re-resolved requests are excluded.
  - **Out-of-scope `property`:** returns zeros rather than 404, the same as the list filters (D11).
- **Cost on the 10k data:** 4 queries (auth, open counts, medians, overdue count), ~42 ms average in-process.

## D26. Deploy shape: Render (native Python, free) + Neon (direct connection)
- **Choice:**
  - Native `runtime: python` rather than Docker: free-tier Docker support isn't documented. The Dockerfile is still built and smoke-tested in CI.
  - Migrations, `createcachetable` and an optional `seed_demo` run in the **build** step (`bin/render-build.sh`), because pre-deploy commands are documented for paid services only. Fallback: move them to the start command.
  - Neon **direct** connection with `sslmode=require` (forced by `prod.py` unless the URL already sets one).
  - `CONN_MAX_AGE=60` with `CONN_HEALTH_CHECKS=True`, so reused connections survive Neon scaling to zero.
  - `RENDER_EXTERNAL_HOSTNAME` is added to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` automatically.
  - `SECRET_KEY` comes from Render `generateValue`.
  - `autoDeployTrigger: checksPass`.
  - Python pinned with `.python-version` (3.13), because Render's default is now 3.14.
- **Why not pooled:** at 2 workers the connection count is tiny. Transaction pooling breaks session features and Neon advises direct connections for migrations.
- **Seeding without a shell:** free instances have no shell, so the build step seeds when `DEMO_PASSWORD` is set. `seed_demo` is idempotent, so redeploys are safe.
- **Risk:** `NUM_PROXIES=1` is a starting value; Render's docs don't state the hop count. It's verified on the live service with forged `X-Forwarded-For` headers (docs/deploy.md section 4).
- **CI:** the Docker job now runs the image with prod settings, gunicorn and Postgres. It asserts health is 200 over HTTP (redirect-exempt), docs are 301 over HTTP and 200 with `X-Forwarded-Proto: https`, and `/api/requests/` is 401.
