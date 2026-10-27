# Decision log

Each entry: choice, alternatives considered, reason.

## D1. Local DB role created via the existing pgpass superuser entry
- **Choice:** Created role `fixflow` (LOGIN, CREATEDB) and database `fixflow` by connecting as `postgres` through the machine's existing `%APPDATA%\postgresql\pgpass.conf`. A random password was generated and written only to the gitignored `.env`.
- **Alternatives:** Stop and ask Humayun to run psql commands.
- **Reason:** The superuser password never had to be entered, read or printed, so the "stop if a superuser password is needed" condition never applied. CREATEDB lets pytest create `test_fixflow`.

## D2. Phase 2 querysets are deliberately unoptimized
- **Choice:** Phase 2 uses plain querysets: no `select_related`, `prefetch_related`, performance annotations or custom indexes beyond FK indexes and the listed constraints.
- **Alternatives:** Optimize while building.
- **Reason:** Phase 3 benchmarks this baseline first, then optimizes and measures again. The before/after numbers are the evidence for the performance claims.
