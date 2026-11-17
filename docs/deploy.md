# Deploying FixFlow: Render (web) + Supabase (Postgres)

**Current setup (2026-11-17):** Render web service on the **Starter** plan (always on),
database on **Supabase Free** through the **session pooler**. Until mid-November the
database was Neon Free; section 1.3 explains why it moved. Provider terms change, so
re-check the linked pages before relying on any number.

## 1. Provider terms that matter here

### 1.1 Render web service
- **Plan: Starter (paid, always on).** Humayun upgraded it in the dashboard, and `render.yaml` now says `plan: starter` so a Blueprint sync doesn't revert it. The service no longer spins down, so there is no cold start.
- **Health checks** ([render.com/docs/health-checks](https://render.com/docs/health-checks)): Render probes `healthCheckPath` every few seconds. `2xx`/`3xx` within 5 s is healthy, and a new deploy only receives traffic once it passes. On an always-on service that means a probe every few seconds, around the clock, so the probe must be cheap (section 1.3).
- **Pre-deploy command** ([render.com/docs/deploys](https://render.com/docs/deploys)): available on paid services. We still run migrations in the build step (section 2), which works on any plan; moving them to `preDeployCommand` is a possible later change, not needed now.
- **Python version:** Render's default is 3.14.x ([render.com/docs/python-version](https://render.com/docs/python-version)). We pin **3.13** with `.python-version` to match CI and the Dockerfile.
- **Blueprint** ([render.com/docs/blueprint-spec](https://render.com/docs/blueprint-spec)): `generateValue: true` creates `SECRET_KEY`; `sync: false` values (`DATABASE_URL`, `DEMO_PASSWORD`) are entered once in the dashboard.
- **Client IP:** Render's docs don't state the `X-Forwarded-For` hop count. **Measured on the live service:** `<client>, <Cloudflare edge>, <Render internal 10.x>`, `REMOTE_ADDR=127.0.0.1`, both proxy entries rotating per request. So **`NUM_PROXIES=3`** (section 4).

### 1.2 Supabase, Free plan
From Supabase's docs, read 2026-11-17:
- **Database size: 500 MB** per project ([pricing](https://supabase.com/pricing), [usage quotas](https://supabase.com/docs/guides/platform/org-based-billing)). The demo is a few hundred rows; the 10k benchmark dataset is never deployed.
- **Egress: 5 GB** included (plus 5 GB cached egress) ([pricing](https://supabase.com/pricing)).
- **Pausing:** "Free projects are paused after 1 week of inactivity" ([pricing](https://supabase.com/pricing)). The docs I read don't define what counts as activity. See the risk note in section 2.
- **Compute (Nano):** up to 60 direct connections, **200 pooler clients**, **default pool size 15** per database role ([compute and disk](https://supabase.com/docs/guides/platform/compute-and-disk)).
- **Connection modes** ([connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres), [pooling and limits](https://supabase.com/docs/guides/database/connecting-to-postgres/pooling-and-limits)):
  - **Session pooler**: `aws-<n>-<region>.pooler.supabase.com`, **port 5432**, user `postgres.<project-ref>`. Full session semantics; Supabase's recommended option for a persistent backend on an IPv4 network.
  - **Transaction pooler**: same host, **port 6543**. Returns the server connection to the pool after every transaction, so session state doesn't survive: no prepared statements, no `SET`, no session advisory locks, no `WITH HOLD` cursors. Meant for serverless and edge functions. **Not for this app** (section 2).
  - **SSL:** Supabase recommends `sslmode=require`.

### 1.3 Why the database moved from Neon (decision D28)
- Neon Free gives **100 CU-hours per project per month** and suspends compute after **5 idle minutes** ([neon.com/docs/introduction/plans](https://neon.com/docs/introduction/plans)). On Render's free plan the web service slept too, so the database idled most of the time.
- After the upgrade to Starter, the web service never sleeps, and Render's health check pointed at `/api/health/`, which runs `SELECT 1`. A query every few seconds, forever, meant Neon **never** reached its 5-minute idle window. The compute ran 24/7 and used up the 100 CU-hours by mid-month.
- Humayun moved the database to Supabase Free (session pooler, West US / Oregon), changed `DATABASE_URL` in Render and redeployed. The build step ran `migrate`, `createcachetable` and `seed_demo` against the new database.
- The code fix is the **liveness / readiness split** below, so the platform's probe never queries the database again, whichever provider hosts it.

## 2. How the app is configured for that

| Concern | Decision |
|---|---|
| Health checks | **Liveness** `/api/health/live/`: returns `{"status":"ok"}` with **no database query** (tested with zero queries and with the DB forced down). Render's `healthCheckPath` points here. **Readiness** `/api/health/`: runs `SELECT 1` and returns 503 if the database is unreachable. Use it for manual checks and the walkthrough, not for a probe that fires every few seconds. |
| Connection mode | **Session pooler, port 5432.** One server connection per client connection for as long as the client stays connected, so `transaction.atomic()` and `SELECT … FOR UPDATE` row locks behave exactly as on a direct connection. **Never the transaction pooler (6543):** `prod.py` refuses to start if `DATABASE_URL` points at `*.pooler.supabase.com:6543` (tested). |
| Connection count | 2 gunicorn sync workers, each holding at most 1 persistent connection, so **about 2 client connections**, plus 1 short-lived one during each build. That's far below the free pooler's 200-client limit and its default pool of 15. |
| TLS to the DB | `prod.py` sets `sslmode=require` unless the URL already sets one. Verified live: readiness reports `database: ok` on Supabase. |
| Connection reuse | `CONN_MAX_AGE=60` avoids a TLS handshake per request. `CONN_HEALTH_CHECKS=True` makes Django test a reused connection before using it, so a connection the pooler closed is replaced instead of failing a request. In session mode a reused connection is the same server session, so this is safe. |
| Migrations | `bin/render-build.sh` runs `migrate`, `createcachetable` and (if `DEMO_PASSWORD` is set) `seed_demo` in the **build** step. A failed build never goes live. |
| Hosts / CSRF | `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` from env, **plus** Render's `RENDER_EXTERNAL_HOSTNAME` and `https://` + that host, added automatically. |
| HTTPS | Render terminates TLS. `SECURE_PROXY_SSL_HEADER` trusts `X-Forwarded-Proto`; `SECURE_SSL_REDIRECT` is on, with **both** health paths exempt (probes use plain HTTP). CI smoke-tests this. |
| Secrets | `SECRET_KEY` generated by Render. `DATABASE_URL` and `DEMO_PASSWORD` entered by Humayun in Render (`sync: false`). Nothing secret is in the repo. |
| Throttle counters | `DatabaseCache` table, created at build. `NUM_PROXIES=3` (measured, section 4). |
| Deploy trigger | `autoDeployTrigger: checksPass`: Render deploys a commit only after GitHub CI passes on it. |
| Workers | 2 gunicorn workers. |

**Risk: Supabase pausing.** With the probe on the liveness path, the database only sees real API traffic. Supabase pauses Free projects after 1 week of inactivity and doesn't say exactly what counts. If the demo goes a week with no visitors, the project could pause, and readiness and every data endpoint would then fail until Humayun restores it in the Supabase dashboard. Mitigation if wanted: a scheduled once-a-day request to `/api/health/` (one `SELECT 1` per day, negligible usage), for example a small GitHub Actions cron. Not added yet; it's Humayun's call.

## 3. Setup steps (accounts and secrets are Humayun's; Claude never sees them)

This is what was done, recorded so the setup can be rebuilt.

### A. Supabase (database)
1. <https://supabase.com> → sign in → **New project**, Free plan. Region **West US (Oregon)**, near the Render service. Set a strong database password and keep it in your password manager.
2. Project → **Connect** → **Session pooler** (not "Transaction pooler", not "Direct" unless you have IPv6 or the IPv4 add-on). Copy the URI. It looks like `postgresql://postgres.<project-ref>:<password>@aws-<n>-<region>.pooler.supabase.com:5432/postgres`. Check that the port is **5432**.
3. Don't paste it into chat. It goes only into Render.

### B. Render (web service)
1. Service `fixflow` → **Environment** → `DATABASE_URL` → paste the session pooler URI → **Save, rebuild and deploy**. The build step migrates and seeds the new database.
2. After a Blueprint change (like this one), check **Settings**: the plan should be **Starter** and the **Health Check Path** should be `/api/health/live/`.
3. Other env values (`DEMO_PASSWORD`, `NUM_PROXIES=3`, `DB_CONN_MAX_AGE=60`) are unchanged.

## 4. Live verification (done 2026-10-29)

These checks ran on Render free + Neon. The proxy-depth check (forged `X-Forwarded-For` logins must hit 429) was repeated after the move to Starter + Supabase; that result and the other post-move checks are in HANDOFF.md.

- `/api/health/` 200, `/api/docs/` 200, `/api/schema/` 200. Plain HTTP → 301 to HTTPS. HSTS and nosniff headers present. Build-step migrations and `seed_demo` reached the database (then Neon): the demo logins work and the seeded requests exist.
- `scripts/walkthrough.py https://fixflow-vzpj.onrender.com`: every step returned the expected status (output in HANDOFF.md).
- **Write throttle (per user)** works across both gunicorn workers: 62 no-op PATCHes as one user gave 60 × 409, then 2 × 429 with `retry_after_seconds`.
- **Login throttle (per IP): a real bug found and fixed.**
  1. **`NUM_PROXIES=1`:** 12 failed logins (11 with forged `X-Forwarded-For`, 1 without) were never throttled.
  2. **`NUM_PROXIES=2`:** 13 rounds of 11 forged-header logins over 21 minutes, never throttled. Humayun confirmed in the dashboard that the value really was 2.
  3. **Evidence:** a temporary, login-only diagnostic endpoint showed the real chain, `<client>, <Cloudflare edge>, <Render internal>`, with both proxy hops changing per request. A forged value is prepended on the left: `<forged>,<client>, <cf>, <render>`. DRF keys on the `NUM_PROXIES`-th entry from the right, so 1 and 2 picked a changing proxy address and every request got a fresh bucket.
  4. **Fix:** `NUM_PROXIES=3` (the client), then the diagnostic was removed. `tests/test_throttling.py` replays this exact chain shape, including a test showing that 2 never throttles.
  5. The re-test after the fix is recorded in HANDOFF.md.
- **Not verified:** whether two *different* real clients get separate buckets (that needs a second network). Optional manual check: after being throttled on Wi-Fi, try logging in from a phone on mobile data. It should not be 429.

## 5. Decision: demo logins are PUBLIC (Humayun chose option b, 2026-10-27)

The architect recommended keeping them private; Humayun chose to publish them.

- The live `DEMO_PASSWORD` is a **new throwaway value, used only for the public demo**. It's not the local `.env` password and not a secret:

  ```
  demo-YVD2LQlb020I
  ```

  In step B4, paste exactly this into Render's `DEMO_PASSWORD` field (ignore the clipboard command, which is for the private option).
- It will be listed in the README with the demo emails (`manager@fixflow.example`, `plumber@…`, `alice@…`, etc.).
- **What visitors can do:** log in as any demo user and create or change demo data within the role rules. They can't reach the admin (demo users aren't staff) or change passwords (no such endpoint). Writes are throttled to 60/min per user and logins to 10/min per IP.
- **Reset:** each deploy re-runs `seed_demo`, which resets the demo passwords and re-creates any missing demo rows. It doesn't delete data visitors added. To wipe the demo, reset the Neon branch (ask first; it drops data).
