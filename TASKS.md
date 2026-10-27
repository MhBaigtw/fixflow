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
- [ ] Repo created + pushed (waiting for Humayun's approval)
- [ ] CI result confirmed

## Phase 3: Measure and harden (not started)
- [ ] seed_bulk (10,000 requests, realistic distributions)
- [ ] Benchmark script: queries/request, p50/p95 per role for list + detail
- [ ] docs/benchmarks.md baseline (hardware, commit, command)
- [ ] Optimize (select_related/prefetch_related, composite indexes)
- [ ] Re-measure + record
- [ ] Query-count regression tests
- [ ] Scoped throttling on login and writes
- [ ] Manager stats endpoint

## Phase 4: Ship (not started)
- [ ] Check Render + Neon free-tier terms
- [ ] Deploy (Humayun creates accounts / enters secrets)
- [ ] Walkthrough script against any base URL
- [ ] Full README + Mermaid architecture diagram
- [ ] docs/resume-bullets.md linked to evidence
