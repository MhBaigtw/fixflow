# Deploying FixFlow: Render (web) + Neon (Postgres)

Checked against the providers' docs on **2026-10-27**. Free tiers change, so re-check
these pages before relying on any number.

## 1. Free-tier terms that matter here

### Render, free web service ([render.com/docs/free](https://render.com/docs/free))
- **Spin-down:** spins down after **15 minutes without inbound traffic**. The next request wakes it, and spin-up takes **about one minute** (browsers see a loading page).
- **Hours:** **750 free instance hours per workspace per calendar month**. A spun-down service uses none. If they run out, free services are suspended until next month.
- **Bandwidth and build minutes:** count against monthly included amounts. Without a payment method, going over suspends free services (bandwidth) or disables builds (build minutes).
- **Other limits:** no persistent disk (local file changes are lost on spin-down), **no shell access**, one instance only, may be restarted at any time, outbound SMTP ports blocked.
- **Render's own free Postgres expires after 30 days.** That's why the database is on Neon.
- **Pre-deploy command:** documented as available for **paid** web services, private services and workers ([render.com/docs/deploys](https://render.com/docs/deploys)). The free page doesn't list it, so **we don't rely on it** (see section 2).
- **Health checks:** `healthCheckPath` in the Blueprint. `2xx`/`3xx` within 5 s counts as healthy, and a new deploy only receives traffic once it passes ([render.com/docs/health-checks](https://render.com/docs/health-checks)). The health-check page doesn't say whether free instances get it; it's harmless if ignored.
- **Docker:** the Blueprint spec allows `runtime: docker`, but no page I found states whether free instances support Docker deploys. **We use the native `python` runtime**, which works for certain and builds faster. The Dockerfile stays for local/CI use (CI builds it and smoke-tests it).
- **Python version:** Render's current default is **3.14.x** ([render.com/docs/python-version](https://render.com/docs/python-version)). We pin **3.13** with a `.python-version` file (a partial version is allowed there) to match CI and the Dockerfile.
- **Blueprint:** `render.yaml` with `plan: free` ([render.com/docs/blueprint-spec](https://render.com/docs/blueprint-spec)).
  - `generateValue: true` creates a random secret, which we use for `SECRET_KEY`.
  - `sync: false` prompts for a value once, at creation.
- **Client IP:** Render's official docs don't state how many hops append to `X-Forwarded-For`. **Measured on the live service:** it arrives as `<client>, <Cloudflare edge>, <Render internal 10.x>` with `REMOTE_ADDR=127.0.0.1`, and both proxy entries change on every request. So **`NUM_PROXIES=3`** (section 4).

### Neon, Free plan ([neon.com/docs/introduction/plans](https://neon.com/docs/introduction/plans))
- **Storage:** **1 GB per project** (20 GB across all projects; up to 100 projects). Inserts, updates and deletes fail when it's full; data is never deleted. The 10k benchmark dataset isn't deployed; only `seed_demo`'s handful of rows.
- **Compute:** **100 CU-hours per project per month** (about 400 h at 0.25 CU). It's suspended until next month if exceeded.
- **Scale to zero:** after **5 minutes idle**, which can't be disabled on Free. The first query after that pays a resume delay (not quantified in the docs; measured in Part F).
- **Network transfer:** 5 GB per project per month.
- **Connections** ([neon.com/docs/connect/connection-pooling](https://neon.com/docs/connect/connection-pooling)):
  - **Pooled** (`-pooler` in the hostname): PgBouncer in **transaction mode**. Session features (SET, LISTEN, SQL-level PREPARE, session advisory locks) don't work.
  - **Direct:** limited by `max_connections` (**104 at 0.25 CU**). Neon recommends direct for migrations.
  - Connection strings include **`sslmode=require`**.

## 2. How the app is configured for that

| Concern | Decision |
|---|---|
| Migrations without pre-deploy | `bin/render-build.sh` runs `migrate`, `createcachetable` and (if `DEMO_PASSWORD` is set) `seed_demo` in the **build** step. A failed build never goes live, so the old version keeps serving. Fallback if the build can't reach the DB: move those lines to the start command (each wake-up then pays roughly a 1–2 s no-op migrate). |
| Pooled vs direct | **Direct** connection string. With 2 gunicorn workers and persistent connections we use ~2 of 104 connections, migrations need a direct connection anyway, and transaction pooling buys nothing at this size. |
| TLS to the DB | `prod.py` sets `sslmode=require` unless the URL already sets an sslmode (`DB_SSLMODE` overrides; only docker-compose sets `disable`). |
| Connection reuse | `CONN_MAX_AGE=60` (`DB_CONN_MAX_AGE`) avoids a TLS handshake per request. `CONN_HEALTH_CHECKS=True` makes Django test a reused connection first, so connections Neon dropped while scaled to zero are replaced instead of failing a request. |
| Hosts / CSRF | `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` from env, **plus** Render's `RENDER_EXTERNAL_HOSTNAME` (set automatically) and `https://` + that host. No values to type for the default `onrender.com` URL. |
| HTTPS | Render terminates TLS. `SECURE_PROXY_SSL_HEADER` trusts `X-Forwarded-Proto`, and `SECURE_SSL_REDIRECT` is on with `/api/health/` exempt. CI smoke-tests this: health 200 over plain HTTP, docs 301 → 200 with `X-Forwarded-Proto: https`. |
| Health check | `/api/health/` (checks the DB; 503 if it's down). |
| Secrets | `SECRET_KEY` generated by Render. `DATABASE_URL` and `DEMO_PASSWORD` are entered by Humayun in Render (`sync: false`). Nothing secret is in the repo. |
| Throttle counters | `DatabaseCache` table, created at build. `NUM_PROXIES` from env (see section 4). |
| Deploy trigger | `autoDeployTrigger: checksPass`: Render deploys a commit only after GitHub CI passes on it. |
| Workers | 2 gunicorn workers (free instance: 0.1 CPU, 512 MB). |

## 3. Steps for Humayun (accounts and secrets are yours; I never see them)

### A. Neon (database)
1. Go to <https://neon.com> → **Sign up** (GitHub or email). Choose the **Free** plan. Don't enter payment details; if asked, stop and tell me.
2. **Create project.**
   - Project name: `fixflow`.
   - Postgres version: **16** (it's what CI tests against; if only 17 is offered, choose 17 and tell me).
   - Region: **AWS US West 2 (Oregon)**, the same area as the Render service.
3. On the project dashboard, click **Connect**.
   - Branch `main`, database `neondb`, role `neondb_owner`.
   - Make sure **Connection pooling is OFF**, so the host has **no** `-pooler`.
   - Copy the connection string. It looks like `postgresql://neondb_owner:…@ep-….us-west-2.aws.neon.tech/neondb?sslmode=require…`.
   - **Don't paste it into chat**; it goes only into Render in step B4.

### B. Render (web service)
1. Go to <https://render.com> → **Get Started** → sign up **with GitHub** as `MhBaigtw`. If it asks for a credit card for the free web service, stop and tell me.
2. Allow Render to access the `MhBaigtw/fixflow` repository (only that repo is fine).
3. Dashboard → **New +** → **Blueprint** → pick `MhBaigtw/fixflow`, branch `main`. Render reads `render.yaml` and shows one service, `fixflow` (plan: Free).
4. It asks for the `sync: false` values:
   - **`DATABASE_URL`:** paste the Neon **direct** string from A3.
   - **`DEMO_PASSWORD`:** depends on the demo-login decision (section 5). To use the same password as your local `.env`, so I can run the walkthrough without ever seeing it, copy it to your clipboard **without printing it**, run from the project folder in PowerShell:
     ```powershell
     (Select-String -Path .env -Pattern '^DEMO_PASSWORD=').Line.Split('=', 2)[1] | Set-Clipboard
     ```
     Then paste into Render. Leave the field empty to skip demo data.
   - Leave **`ALLOWED_HOSTS`** and **`CSRF_TRUSTED_ORIGINS`** empty. The `onrender.com` host is added automatically; they're only for a custom domain later.
   - **`SECRET_KEY`** is generated by Render, so there's nothing to do. (If you ever need one yourself, generate it straight to the clipboard: `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(50))" | Set-Clipboard`.)
5. Click **Apply**. The first build takes a few minutes. When it shows **Live**, copy the service URL (e.g. `https://fixflow-xxxx.onrender.com`) and send me **only the URL**.

## 4. Live verification (done 2026-10-29)

- `/api/health/` 200, `/api/docs/` 200, `/api/schema/` 200. Plain HTTP → 301 to HTTPS. HSTS and nosniff headers present. Build-step migrations and `seed_demo` reached Neon: the demo logins work and the seeded requests exist.
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
