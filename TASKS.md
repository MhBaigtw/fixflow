# TASKS

## Step 0: Plan
- [x] SPEC.md, TASKS.md, CLAUDE.md, HANDOFF.md, docs/decisions.md

## Phase 1: Foundation
- [ ] Settings package base/dev/test/prod, env-driven; .env.example; prod hardening
- [ ] Apps core, accounts, properties
- [ ] Custom User (email login, role) before first migration
- [ ] Property, Unit, Tenancy models + constraints
- [ ] JWT token obtain/refresh (env-configurable lifetimes)
- [ ] GET /api/me/
- [ ] properties/ CRUD (own only)
- [ ] properties/{id}/units/ list/create
- [ ] units/{id}/tenancies/ attach by email (400 for non-tenant / duplicate)
- [ ] tenancies/{id}/end/
- [ ] health/ (200 / 503)
- [ ] schema/ + docs/
- [ ] Custom exception handler (single error shape)
- [ ] Pagination (20, max 100)
- [ ] Admin registration
- [ ] seed_demo (idempotent, DEMO_PASSWORD)
- [ ] Dockerfile + docker-compose.yml
- [ ] CI workflow
- [ ] Phase 1 tests

## Phase 2: Core workflow
- [ ] maintenance app: MaintenanceRequest, RequestEvent, Comment
- [ ] SLA computation from settings; overdue definition
- [ ] Transition table + atomic, locked transition service
- [ ] Initial event on creation
- [ ] Visibility selectors + permissions
- [ ] requests/ list/create, detail/PATCH
- [ ] requests/{id}/transition/
- [ ] requests/{id}/events/
- [ ] requests/{id}/comments/ (internal hidden from tenants)
- [ ] Filters + ordering
- [ ] seed_demo includes requests in varied states
- [ ] Phase 2 tests (transition matrix, reopen window, audit, visibility, comments, SLA, concurrency, edit rules)
- [ ] scripts/walkthrough.py

## Checkpoint 1
- [ ] Full verification run
- [ ] README, TASKS, HANDOFF updated
- [ ] Repo created + pushed (needs approval)
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
