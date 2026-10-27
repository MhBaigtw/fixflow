"""EXPLAIN (ANALYZE, BUFFERS) the exact SQL the list endpoints emit, on the dev database.

Usage:
    .venv\\Scripts\\python.exe scripts\\explain_queries.py            # summary per query
    .venv\\Scripts\\python.exe scripts\\explain_queries.py --full     # full plans too

For each scenario it calls the real endpoint in-process, captures every SQL query
it ran (except the JWT user lookup), runs VACUUM ANALYZE first so planner
statistics are current, then EXPLAINs each captured query 5 times and reports the
median execution time and the scan nodes. Subjects are the same as in
scripts/benchmark.py.

The captured SQL is the app's own, already-parameterised query text as executed
by psycopg; this is a local dev tool and never takes outside input.
"""

import os
import re
import statistics
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
warnings.filterwarnings("ignore", message="No directory at")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

from scripts.benchmark import client_for, pick_subjects  # noqa: E402

TABLES = [
    "maintenance_maintenancerequest",
    "maintenance_requestevent",
    "maintenance_comment",
    "properties_unit",
    "properties_property",
    "properties_tenancy",
    "accounts_user",
]
SCAN = re.compile(
    r"((?:Parallel )?(?:Seq Scan|Index Scan|Index Only Scan|Bitmap Index Scan|"
    r"Bitmap Heap Scan)(?: Backward)?(?: using \w+)? on \w+)"
)


def scenarios():
    manager, tenant, contractor, _, _ = pick_subjects()
    return [
        ("manager list", manager, "/api/requests/"),
        ("tenant list", tenant, "/api/requests/"),
        ("contractor list", contractor, "/api/requests/"),
        ("manager list ?overdue=true", manager, "/api/requests/?overdue=true"),
        ("manager list ?ordering=sla_due_at", manager, "/api/requests/?ordering=sla_due_at"),
        ("contractor list ?ordering=priority", contractor, "/api/requests/?ordering=priority"),
    ]


def main():
    full = "--full" in sys.argv
    with connection.cursor() as cur:
        for table in TABLES:
            cur.execute(f"VACUUM ANALYZE {table}")  # fixed list of our own table names
    for name, user, url in scenarios():
        client = client_for(user)
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(url).status_code == 200
        sqls = [q["sql"] for q in ctx.captured_queries if '"accounts_user"' not in q["sql"][:200]]
        for i, sql in enumerate(sqls, 1):
            kind = "count" if sql.lstrip().upper().startswith("SELECT COUNT") else "page"
            plans = []
            with connection.cursor() as cur:
                for _ in range(5):
                    cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql)
                    plans.append("\n".join(row[0] for row in cur.fetchall()))
            times = [float(re.search(r"Execution Time: ([\d.]+)", p).group(1)) for p in plans]
            plan = plans[-1]
            print(
                f"### {name} [{kind} query {i}]: median execution {statistics.median(times):.2f} ms"
            )
            print(f"    {plan.splitlines()[0].strip()}")
            for scan in sorted(set(SCAN.findall(plan))):
                print(f"    - {scan}")
            if full:
                print(sql)
                print(plan)
            print()


if __name__ == "__main__":
    main()
