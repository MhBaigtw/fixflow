"""Try candidate indexes without keeping them: each is created inside a transaction,
the endpoint queries are EXPLAINed (ANALYZE, BUFFERS), then everything is rolled back.

Usage:
    .venv\\Scripts\\python.exe scripts\\index_experiment.py

Prints, per candidate and per affected query: median execution time without vs
with the index, and whether the planner actually used it. Only indexes that show a
real benefit here get a migration.
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

from django.db import connection, transaction  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

from scripts.benchmark import client_for, pick_subjects  # noqa: E402

T = "maintenance_maintenancerequest"
# name -> (DDL, scenario names it is meant to help). Fixed strings, no outside input.
CANDIDATES = {
    "created_at_id": (
        f"CREATE INDEX exp_created ON {T} (created_at DESC, id DESC)",
        ["manager list", "manager list ?overdue=true", "tenant list"],
    ),
    "assigned_created": (
        f"CREATE INDEX exp_assigned ON {T} (assigned_to_id, created_at DESC, id DESC)",
        ["contractor list"],
    ),
    "sla_due_at": (
        f"CREATE INDEX exp_sla ON {T} (sla_due_at)",
        ["manager list ?ordering=sla_due_at"],
    ),
    "open_sla_partial": (
        f"CREATE INDEX exp_open_sla ON {T} (sla_due_at) "
        "WHERE status NOT IN ('resolved', 'closed', 'cancelled')",
        ["manager list ?overdue=true"],
    ),
}


class Rollback(Exception):
    pass


def scenario_sql():
    manager, tenant, contractor, _, _ = pick_subjects()
    urls = {
        "manager list": (manager, "/api/requests/"),
        "tenant list": (tenant, "/api/requests/"),
        "contractor list": (contractor, "/api/requests/"),
        "manager list ?overdue=true": (manager, "/api/requests/?overdue=true"),
        "manager list ?ordering=sla_due_at": (manager, "/api/requests/?ordering=sla_due_at"),
    }
    out = {}
    for name, (user, url) in urls.items():
        with CaptureQueriesContext(connection) as ctx:
            assert client_for(user).get(url).status_code == 200
        out[name] = [
            q["sql"] for q in ctx.captured_queries if '"accounts_user"' not in q["sql"][:200]
        ]
    return out


def timed_plan(sql):
    plans = []
    with connection.cursor() as cur:
        for _ in range(7):
            cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql)
            plans.append("\n".join(r[0] for r in cur.fetchall()))
    times = [float(re.search(r"Execution Time: ([\d.]+)", p).group(1)) for p in plans]
    return statistics.median(times), plans[-1]


def main():
    sqls = scenario_sql()
    baseline = {name: [timed_plan(s) for s in qs] for name, qs in sqls.items()}
    for cand, (ddl, targets) in CANDIDATES.items():
        index_name = ddl.split()[2]
        print(f"=== {cand}: {ddl}")
        try:
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(ddl)
                    cur.execute(f"ANALYZE {T}")
                for name in targets:
                    for i, sql in enumerate(sqls[name]):
                        kind = (
                            "count" if sql.lstrip().upper().startswith("SELECT COUNT") else "page"
                        )
                        before_ms, _ = baseline[name][i]
                        after_ms, plan = timed_plan(sql)
                        used = index_name in plan
                        print(
                            f"  {name} [{kind}]: {before_ms:.2f} ms -> {after_ms:.2f} ms "
                            f"(index used: {used})"
                        )
                        if used:
                            line = next(ln for ln in plan.splitlines() if index_name in ln)
                            print(f"      {line.strip()}")
                raise Rollback
        except Rollback:
            pass
        print()


if __name__ == "__main__":
    main()
