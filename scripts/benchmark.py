"""In-process API benchmark against the dev database (seed it with seed_bulk first).

Usage:
    .venv\\Scripts\\python.exe scripts\\benchmark.py --label Baseline
    .venv\\Scripts\\python.exe scripts\\benchmark.py --label Optimized --compare-to Baseline

For each endpoint/role:
- SQL queries for one request (CaptureQueriesContext)
- then 20 warmup requests
- then 200 timed requests, reporting p50 / p95 / max in milliseconds.

Latency is measured IN-PROCESS with Django's test client: no network, no gunicorn,
and one persistent DB connection (the test client keeps it open, like
CONN_MAX_AGE > 0). It measures application + database time only. DEBUG is forced
to False while timing so Django doesn't keep a query log.

Results are appended to docs/benchmarks.md, and raw numbers are saved to
docs/benchmark-data/<label>.json for later comparison.
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path

# WhiteNoise warns when STATIC_ROOT doesn't exist (no collectstatic in dev); irrelevant here.
warnings.filterwarnings("ignore", message="No directory at")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

import rest_framework  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import connection  # noqa: E402
from django.db.models import Count  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402
from rest_framework_simplejwt.tokens import RefreshToken  # noqa: E402

from apps.accounts.models import User  # noqa: E402
from apps.maintenance.models import Comment, MaintenanceRequest, RequestEvent  # noqa: E402
from apps.maintenance.selectors import requests_visible_to  # noqa: E402
from apps.properties.models import Tenancy  # noqa: E402

DATA_DIR = ROOT / "docs" / "benchmark-data"
REPORT = ROOT / "docs" / "benchmarks.md"


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def machine():
    if platform.system() == "Windows":
        ps = (
            "$c=(Get-CimInstance Win32_Processor | Select-Object -First 1);"
            "$m=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
            "$o=(Get-CimInstance Win32_OperatingSystem);"
            '"$($c.Name.Trim())|$($c.NumberOfCores)|$($c.NumberOfLogicalProcessors)|$m|'
            '$($o.Caption) $($o.Version)"'
        )
        out = sh("powershell", "-NoProfile", "-Command", ps)
        if out.count("|") == 4:
            cpu, cores, threads, mem, os_name = out.split("|")
            ram = f"{int(mem) / 1024**3:.1f} GB"
            return f"{cpu} ({cores} cores / {threads} threads), {ram} RAM, {os_name}"
    return f"{platform.processor() or platform.machine()}, {platform.platform()}"


def git_commit():
    commit = sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD") or "unknown"
    dirty = sh("git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no")
    return commit + ("-dirty" if dirty else "")


def pg_version():
    with connection.cursor() as cur:
        cur.execute("SHOW server_version")
        return cur.fetchone()[0]


def pick_subjects():
    """Deterministic choice of the busiest users and objects (ties broken by id)."""
    manager = (
        User.objects.filter(role=User.Role.MANAGER)
        .annotate(n=Count("managed_properties__units__maintenance_requests"))
        .order_by("-n", "id")
        .first()
    )
    busiest_unit = (
        MaintenanceRequest.objects.values("unit_id")
        .annotate(n=Count("id"))
        .order_by("-n", "unit_id")
        .first()["unit_id"]
    )
    tenant = (
        Tenancy.objects.filter(unit_id=busiest_unit, end_date__isnull=True)
        .order_by("id")
        .first()
        .tenant
    )
    contractor = (
        User.objects.filter(role=User.Role.CONTRACTOR)
        .annotate(n=Count("assigned_requests"))
        .order_by("-n", "id")
        .first()
    )
    scope = requests_visible_to(manager)
    by_events = scope.annotate(n=Count("events")).order_by("-n", "id").first()
    by_comments = scope.annotate(n=Count("comments")).order_by("-n", "id").first()
    return manager, tenant, contractor, by_events, by_comments


def latest_visible(user):
    return requests_visible_to(user).order_by("-created_at", "-id").first()


def client_for(user):
    client = APIClient(HTTP_HOST="localhost")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def measure(client, url, warmup, runs):
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url)
    assert resp.status_code == 200, (url, resp.status_code, resp.content[:200])
    queries = len(ctx.captured_queries)
    for _ in range(warmup):
        client.get(url)
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        client.get(url)
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    return {
        "queries": queries,
        "p50": statistics.median(times),
        "p95": statistics.quantiles(times, n=100, method="inclusive")[94],
        "max": times[-1],
    }


def fmt(x):
    return f"{x:.1f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--compare-to")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--no-write", action="store_true", help="Print only.")
    args = parser.parse_args()

    settings.DEBUG = False  # no per-query logging overhead while timing
    manager, tenant, contractor, by_events, by_comments = pick_subjects()
    rows = []
    for role, user in (("manager", manager), ("tenant", tenant), ("contractor", contractor)):
        client = client_for(user)
        visible = requests_visible_to(user).count()
        rows.append(
            (f"GET /api/requests/ (page 1, 20 rows of {visible})", role, "/api/requests/", client)
        )
        detail = latest_visible(user)
        rows.append(("GET /api/requests/{id}/", role, f"/api/requests/{detail.id}/", client))
    mclient = client_for(manager)
    n_events = by_events.events.count()
    n_comments = by_comments.comments.count()
    rows.append(
        (
            f"GET /api/requests/{{id}}/events/ ({n_events} events)",
            "manager",
            f"/api/requests/{by_events.id}/events/",
            mclient,
        )
    )
    rows.append(
        (
            f"GET /api/requests/{{id}}/comments/ ({n_comments} comments)",
            "manager",
            f"/api/requests/{by_comments.id}/comments/",
            mclient,
        )
    )

    results = []
    for name, role, url, client in rows:
        r = measure(client, url, args.warmup, args.runs)
        results.append({"endpoint": name, "role": role, **r})
        print(
            f"{name:<55} {role:<10} q={r['queries']:<3} p50={fmt(r['p50'])} "
            f"p95={fmt(r['p95'])} max={fmt(r['max'])}"
        )

    meta = {
        "label": args.label,
        "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "commit": git_commit(),
        "machine": machine(),
        "software": (
            f"Python {platform.python_version()}, Django {django.get_version()}, "
            f"DRF {rest_framework.VERSION}, PostgreSQL {pg_version()} (local, same machine)"
        ),
        "dataset": (
            f"{MaintenanceRequest.objects.count():,} requests, "
            f"{RequestEvent.objects.count():,} events, {Comment.objects.count():,} comments, "
            f"{User.objects.count():,} users"
        ),
        "subjects": (
            f"manager={manager.email}, tenant={tenant.email}, contractor={contractor.email}"
        ),
        "command": (
            f"{Path('.venv/Scripts/python.exe')} scripts/benchmark.py --label {args.label}"
            + (f" --compare-to {args.compare_to}" if args.compare_to else "")
        ),
        "warmup": args.warmup,
        "runs": args.runs,
    }

    lines = [
        f"\n## {args.label} ({meta['date']})\n",
        f"- Commit: `{meta['commit']}`",
        f"- Command: `{meta['command']}`",
        f"- Machine: {meta['machine']}",
        f"- Software: {meta['software']}",
        f"- Dataset: {meta['dataset']} (`seed_bulk --seed 1` plus `seed_demo`)",
        f"- Subjects: {meta['subjects']}",
        f"- Method: in-process Django test client: **no network, no gunicorn**, persistent DB "
        f"connection, DEBUG=False. 1 counted request for SQL queries, {args.warmup} warmup, "
        f"{args.runs} timed. Latency is application + database time only.",
        "",
        "| Endpoint | Role | SQL queries | p50 ms | p95 ms | max ms |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['endpoint']} | {r['role']} | {r['queries']} | {fmt(r['p50'])} | "
            f"{fmt(r['p95'])} | {fmt(r['max'])} |"
        )

    if args.compare_to:
        before = json.loads((DATA_DIR / f"{args.compare_to}.json").read_text())["results"]
        lines += [
            "",
            f"### {args.compare_to} -> {args.label}",
            "",
            "| Endpoint | Role | Queries | p50 ms | p95 ms |",
            "|---|---|---|---|---|",
        ]
        for b, a in zip(before, results, strict=True):
            assert (b["endpoint"], b["role"]) == (a["endpoint"], a["role"])
            lines.append(
                f"| {a['endpoint']} | {a['role']} | {b['queries']} -> {a['queries']} | "
                f"{fmt(b['p50'])} -> {fmt(a['p50'])} | {fmt(b['p95'])} -> {fmt(a['p95'])} |"
            )

    text = "\n".join(lines) + "\n"
    print(text)
    if not args.no_write:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / f"{args.label}.json").write_text(
            json.dumps({"meta": meta, "results": results}, indent=2) + "\n", encoding="utf-8"
        )
        if not REPORT.exists():
            REPORT.write_text(
                "# Benchmarks\n\nGenerated by `scripts/benchmark.py`. See the script docstring "
                "for the method.\n",
                encoding="utf-8",
            )
        with REPORT.open("a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
