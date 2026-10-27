# CLAUDE.md: rules for every session on FixFlow

Read SPEC.md (what to build), TASKS.md (where we are) and docs/decisions.md (why) first.

## Environment (Windows)

- Python: always use the venv interpreter: `.\.venv\Scripts\python.exe -m pip|pytest|...`. Bare `python` fails (Microsoft Store alias). Create the venv with `py -m venv .venv`.
- Postgres 16 runs natively (service `postgresql-x64-16`). psql is at `C:\Program Files\PostgreSQL\16\bin\psql.exe` (not on PATH). Local dev must NOT require Docker.
- Secrets live in `.env` (gitignored). Never print, log or commit passwords, tokens or `.env` contents.

## Conventions

- Business rules live in services (`apps/<app>/services.py` or a `services/` package). Views and serializers stay thin.
- Read-side query logic lives in `selectors.py`.
- Object scoping lives in querysets plus permission classes, never ad hoc per view.
- Out-of-scope objects return 404, not 403, so existence is never leaked. A wrong role on an allowed resource type returns 403.
- One error shape everywhere: `{"error": {"code": "<machine_code>", "message": "<human message>", "details": {...}}}`.
- Time is stored in UTC. Durations are computed in the database where practical.
- Every business rule has a test. No float money anywhere.
- No speculative abstractions, no base-class hierarchies, no plugin systems.
- Maintenance status changes ONLY through `apps/maintenance/services/transitions.py`.
- RequestEvent is append-only: never update or delete events from code.

## Working rules

- Fix failing tests by fixing code, never by weakening or deleting the test. If a test is wrong, explain why in HANDOFF.md.
- Never claim something works without having run it.
- Conventional commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`).
- Ask before: creating GitHub repos, pushing, deleting anything outside this folder, dropping databases, force-pushing, anything paid.

## Commands

```powershell
.\.venv\Scripts\python.exe -m pytest                     # tests + coverage (fails under 85%)
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format .
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py runserver
.\.venv\Scripts\python.exe manage.py seed_demo
```
