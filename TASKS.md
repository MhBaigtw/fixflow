# TASKS

## Step 0: Plan
- [x] SPEC.md, TASKS.md, CLAUDE.md, HANDOFF.md, docs/decisions.md

## Phase 1: Foundation
- [x] Settings package base/dev/test/prod, env-driven; .env.example; prod hardening
- [x] Apps core, accounts, properties
- [x] Custom User (email login, role) before first migration
- [x] Property, Unit, Tenancy models + constraints
- [x] JWT token obtain/refresh (env-configurable lifetimes)
- [x] GET /api/me/
- [x] properties/ list/create/retrieve/update (own only)
- [x] properties/{id}/units/ list/create
- [x] units/{id}/tenancies/ attach by email (400 for non-tenant / duplicate), plus GET list
- [x] tenancies/{id}/end/
- [x] health/ (200 / 503)
- [x] schema/ + docs/
- [x] Custom exception handler (single error shape)
- [x] Pagination (20, max 100)
- [x] Admin registration
- [x] seed_demo (idempotent, DEMO_PASSWORD)
- [x] Dockerfile + docker-compose.yml (not run locally; CI builds the image)
- [x] CI workflow
- [x] Phase 1 tests

## Phase 2: Core workflow
- [x] maintenance app: MaintenanceRequest, RequestEvent, Comment
- [x] SLA computation from settings; overdue definition
- [x] Transition table + atomic, locked transition service
- [x] Initial event on creation
- [x] Visibility selectors + permissions
- [x] requests/ list/create, detail/PATCH
- [x] requests/{id}/transition/
- [x] requests/{id}/events/
- [x] requests/{id}/comments/ (internal hidden from tenants)
- [x] Filters + ordering
- [x] seed_demo includes requests in varied states
- [x] Phase 2 tests (transition matrix, reopen window, audit, visibility, comments, SLA, concurrency, edit rules)
- [x] scripts/walkthrough.py

## Checkpoint 1
- [x] Full verification run
- [x] README, TASKS, HANDOFF updated
- [x] Repo created + pushed (https://github.com/MhBaigtw/fixflow)
- [x] CI result confirmed (green on 8ad1a3a after fixing an order-dependent race test)

## Checkpoint 2

### Part A: Architect review fixes
- [x] A1 Case-insensitive login (custom token serializer)
- [x] A2 requested_priority vs priority; manager may change priority (PATCH + triage)
- [x] A3 Edit events (event_type, changes); no-op PATCH writes nothing
- [x] A4 Unit label race -> 400
- [x] A5 Concurrency test docstring

### Part B: Baseline measurement (Phase 3)
- [x] seed_bulk (10,000 requests, realistic distributions, idempotent per --seed)
- [x] scripts/benchmark.py (queries + p50/p95/max per role/endpoint)
- [x] docs/benchmarks.md "Baseline" committed before any optimization

### Part C: Optimize and re-measure (Phase 3)
- [x] Remove N+1 (select_related / prefetch), cheaper transition permission checks
- [x] Indexes only where EXPLAIN (ANALYZE, BUFFERS) shows a benefit
- [x] "Optimized" benchmark + before/after comparison
- [x] Query-count regression tests

### Part D: Hardening and stats (Phase 3)
- [x] Scoped throttling (login/refresh per IP, writes per user), env-configurable
- [x] DatabaseCache for shared throttle counters; tests isolated
- [x] GET /api/stats/ (open by status, overdue, median hours to resolve per category)
- [x] Full verification, push (ask first), CI green

### Part E: Deploy prep (Phase 4)
- [x] docs/deploy.md: current Render + Neon free-tier terms with links
- [x] render.yaml / settings for deploy (SSL DB, CSRF origins, CONN_MAX_AGE, cache table)
- [x] Click-by-click steps for Humayun
- [x] Ask Humayun: demo logins private or public (chose public)
- [x] STOP for Humayun

### Part F: Deploy and finish (Phase 4, after "continue")
- [x] Deploy; verify health/docs; seed_demo live; walkthrough against live URL
- [x] Live throttle check; fix NUM_PROXIES (1 and 2 wrong, 3 measured)
- [x] README: live URL, Mermaid diagram, results table, demo-login choice
- [x] docs/resume-bullets.md (every number linked to evidence)
- [ ] Final verification, push, CI green, HANDOFF Checkpoint 2
