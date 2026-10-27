"""Bulk benchmark dataset (Phase 3). Not for production: refuses to run unless DEBUG
is True or --force is given.

Idempotent per --seed: all rows are tagged with the seed (emails, property names);
if the seed's data already exists, the command reports counts and does nothing.
All randomness comes from random.Random(seed); timestamps are relative to "now".

It uses bulk_create and bypasses the services (for speed). Every row still obeys the
model rules: unique (property, label), one active tenancy per (unit, tenant), a
legal and gap-free event chain per request, and workflow fields consistent with
the status.

Default distributions (sizes overridable with flags):
- Users: 3 managers; 30 contractors; ~700 tenants.
  - 1 primary tenant per unit.
  - A roommate on ~65% of units.
  - A former (ended) tenant on ~9% of units, i.e. ~5% of all tenancies have ended.
  - Seeded users get an unusable password (nobody logs in as them).
- Properties: 25, assigned to managers with weights 50/30/20 (one manager-heavy
  account). ~400 units in total, at least 6 per property, the rest spread at random.
- Requests: 10,000.
  - Status: closed 45%, resolved 15%, cancelled 10%, submitted 8%, triaged 7%,
    assigned 7%, in_progress 8%.
  - Priority: emergency 10%, urgent 30%, routine 60%.
  - Category: plumbing 25%, appliance 20%, electrical 15%, heating 15%, other 10%,
    pest 8%, structural 7%.
  - Units are picked with Pareto(1.5) weights, so a few units are very busy.
    Contractors are picked with Pareto(1.2) weights, so a few carry most of the work.
- Timing:
  - created_at for terminal requests (closed/resolved/cancelled) is uniform over
    the last 12 months. Open requests are recent: an exponential age, mean 8 days,
    capped at 60 days. This gives a realistic overdue share instead of almost every
    open request being months late.
  - Step durations are scaled by priority (emergency x0.2, urgent x0.5, routine x1)
    and, for the repair itself, by category (structural slowest, other fastest).
  - 10% of closed requests went through one reopen cycle (resolved -> triaged ->
    assigned -> in_progress -> resolved -> closed), reopened within 13 days.
- Cancelled requests: 60% from submitted, 25% from triaged, 15% from assigned.
- Comments: 0-5 per request (weights 25/25/20/15/10/5), by the creator, the
  manager or the contractor. Only non-tenants post internal comments (50% of
  theirs).
"""

import random
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.maintenance.models import Comment, MaintenanceRequest, RequestEvent
from apps.properties.models import Property, Tenancy, Unit

S = MaintenanceRequest.Status
P = MaintenanceRequest.Priority
C = MaintenanceRequest.Category

STATUS_WEIGHTS = {
    S.CLOSED: 45,
    S.RESOLVED: 15,
    S.CANCELLED: 10,
    S.SUBMITTED: 8,
    S.TRIAGED: 7,
    S.ASSIGNED: 7,
    S.IN_PROGRESS: 8,
}
PRIORITY_WEIGHTS = {P.EMERGENCY: 10, P.URGENT: 30, P.ROUTINE: 60}
CATEGORY_WEIGHTS = {
    C.PLUMBING: 25,
    C.APPLIANCE: 20,
    C.ELECTRICAL: 15,
    C.HEATING: 15,
    C.OTHER: 10,
    C.PEST: 8,
    C.STRUCTURAL: 7,
}
PRIORITY_SPEED = {P.EMERGENCY: 0.2, P.URGENT: 0.5, P.ROUTINE: 1.0}
REPAIR_SPEED = {
    C.STRUCTURAL: 2.0,
    C.PEST: 1.5,
    C.APPLIANCE: 1.2,
    C.HEATING: 1.1,
    C.PLUMBING: 1.0,
    C.ELECTRICAL: 0.9,
    C.OTHER: 0.8,
}
OPEN_STATUSES = {S.SUBMITTED, S.TRIAGED, S.ASSIGNED, S.IN_PROGRESS}

# Paths (sequences of actions) that end in each status.
FORWARD = ["triage", "assign", "start", "resolve"]
PATHS = {
    S.SUBMITTED: [],
    S.TRIAGED: FORWARD[:1],
    S.ASSIGNED: FORWARD[:2],
    S.IN_PROGRESS: FORWARD[:3],
    S.RESOLVED: FORWARD,
}
TO_STATUS = {
    "triage": S.TRIAGED,
    "assign": S.ASSIGNED,
    "start": S.IN_PROGRESS,
    "resolve": S.RESOLVED,
    "close": S.CLOSED,
    "reopen": S.TRIAGED,
    "cancel": S.CANCELLED,
}
BATCH = 2000


def weighted(rng, mapping):
    return rng.choices(list(mapping), weights=list(mapping.values()))[0]


class Command(BaseCommand):
    help = "Seed a large, realistic dataset for benchmarking (DEBUG only unless --force)."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=1)
        parser.add_argument("--requests", type=int, default=10_000)
        parser.add_argument("--managers", type=int, default=3)
        parser.add_argument("--properties", type=int, default=25)
        parser.add_argument("--units", type=int, default=400)
        parser.add_argument("--contractors", type=int, default=30)
        parser.add_argument("--force", action="store_true", help="Allow when DEBUG is False.")

    def handle(self, *args, **opts):
        if not (settings.DEBUG or opts["force"]):
            raise CommandError("Refusing to bulk-seed with DEBUG=False. Pass --force to override.")
        if opts["units"] < opts["properties"]:
            raise CommandError("--units must be at least --properties.")

        seed = opts["seed"]
        marker = f"Bulk {seed} Property 01"
        if Property.objects.filter(name=marker).exists():
            self.stdout.write(f"Seed {seed} already present; nothing to do.")
            self.report()
            return

        started = time.perf_counter()
        self.rng = random.Random(seed)
        self.now = timezone.now()
        self.today = timezone.localdate()
        with transaction.atomic():
            created = self.build(seed, opts)
        elapsed = time.perf_counter() - started

        self.stdout.write(self.style.SUCCESS(f"Seed {seed} created in {elapsed:.1f}s:"))
        for name, n in created.items():
            self.stdout.write(f"  {name:<12} +{n}")
        self.report()

    def report(self):
        models = [User, Property, Unit, Tenancy, MaintenanceRequest, RequestEvent, Comment]
        totals = ", ".join(f"{m.__name__}={m.objects.count()}" for m in models)
        self.stdout.write(f"Database totals: {totals}")

    # --- building blocks -------------------------------------------------------

    def build(self, seed, opts):
        rng = self.rng
        tag = f"bulk{seed}"
        unusable = make_password(None)

        def users(role, prefix, n):
            objs = [
                User(
                    email=f"{prefix}{i}@{tag}.example",
                    role=role,
                    first_name=prefix.title(),
                    last_name=str(i),
                    password=unusable,
                )
                for i in range(n)
            ]
            return User.objects.bulk_create(objs, batch_size=BATCH)

        managers = users(User.Role.MANAGER, "manager", opts["managers"])
        contractors = users(User.Role.CONTRACTOR, "contractor", opts["contractors"])

        # Properties, unevenly split across managers (first manager is the busiest).
        manager_weights = [0.5, 0.3, 0.2] + [0.1] * max(0, len(managers) - 3)
        props = Property.objects.bulk_create(
            [
                Property(
                    name=f"Bulk {seed} Property {i + 1:02d}",
                    address=f"{100 + i} Benchmark Avenue",
                    manager=rng.choices(managers, weights=manager_weights[: len(managers)])[0],
                    created_at=self.now - timedelta(days=900),
                )
                for i in range(opts["properties"])
            ]
        )

        # Units: at least 6 per property (or as many as fit), the rest spread at random.
        n_props, n_units = len(props), opts["units"]
        base = min(6, n_units // n_props)
        counts = [base] * n_props
        for _ in range(n_units - base * n_props):
            counts[rng.randrange(n_props)] += 1
        units = Unit.objects.bulk_create(
            [
                Unit(property=prop, label=f"U{j + 1:03d}")
                for prop, count in zip(props, counts, strict=True)
                for j in range(count)
            ],
            batch_size=BATCH,
        )

        # Tenancies: a primary tenant per unit, roommates, and some former tenants.
        plan = []  # (unit, start, end)
        for unit in units:
            plan.append((unit, self.today - timedelta(days=rng.randint(380, 900)), None))
            if rng.random() < 0.65:
                plan.append((unit, self.today - timedelta(days=rng.randint(370, 700)), None))
            if rng.random() < 0.09:
                start = self.today - timedelta(days=rng.randint(500, 900))
                plan.append((unit, start, self.today - timedelta(days=rng.randint(30, 200))))
        tenants = users(User.Role.TENANT, "tenant", len(plan))
        tenancies = Tenancy.objects.bulk_create(
            [
                Tenancy(unit=unit, tenant=tenant, start_date=start, end_date=end)
                for (unit, start, end), tenant in zip(plan, tenants, strict=True)
            ],
            batch_size=BATCH,
        )
        self.tenancies_by_unit = {}
        for t in tenancies:
            self.tenancies_by_unit.setdefault(t.unit_id, []).append(t)

        # Requests, their event chains and comments.
        self.unit_weights = [rng.paretovariate(1.5) for _ in units]
        self.contractor_weights = [rng.paretovariate(1.2) for _ in contractors]
        self.units, self.contractors = units, contractors
        self.manager_of = {p.id: p.manager for p in props}

        specs = [self.make_request() for _ in range(opts["requests"])]
        requests = MaintenanceRequest.objects.bulk_create(
            [spec["request"] for spec in specs], batch_size=BATCH
        )
        events, comments = [], []
        for req, spec in zip(requests, specs, strict=True):
            for event in spec["events"]:
                event.request_id = req.pk
                events.append(event)
            for comment in spec["comments"]:
                comment.request_id = req.pk
                comments.append(comment)
        RequestEvent.objects.bulk_create(events, batch_size=BATCH)
        Comment.objects.bulk_create(comments, batch_size=BATCH)

        return {
            "users": len(managers) + len(contractors) + len(tenants),
            "properties": len(props),
            "units": len(units),
            "tenancies": len(tenancies),
            "requests": len(requests),
            "events": len(events),
            "comments": len(comments),
        }

    def active_at(self, tenancy, when):
        day = when.date() if hasattr(when, "date") else when
        return tenancy.start_date <= day and (tenancy.end_date is None or day < tenancy.end_date)

    def make_request(self):
        rng, now = self.rng, self.now
        status = weighted(rng, STATUS_WEIGHTS)
        priority = weighted(rng, PRIORITY_WEIGHTS)
        category = weighted(rng, CATEGORY_WEIGHTS)
        unit = rng.choices(self.units, weights=self.unit_weights)[0]
        manager = self.manager_of[unit.property_id]

        if status in OPEN_STATUSES:
            # At least 2h old so the squeezed timeline below always has room.
            age = timedelta(hours=min(max(rng.expovariate(1 / (8 * 24)), 2), 60 * 24))
        else:
            age = timedelta(days=rng.uniform(1, 365))
        created_at = now - age

        unit_tenancies = self.tenancies_by_unit[unit.id]
        candidates = [t for t in unit_tenancies if self.active_at(t, created_at)]
        creator_tenancy = rng.choice(candidates)
        creator = creator_tenancy.tenant
        current_tenant = next(t.tenant for t in unit_tenancies if t.end_date is None)

        if status == S.CLOSED:
            path = FORWARD + (
                ["reopen", "assign", "start", "resolve"] if rng.random() < 0.1 else []
            )
            path = path + ["close"]
        elif status == S.CANCELLED:
            path = rng.choices([[], ["triage"], ["triage", "assign"]], weights=[60, 25, 15])[0]
            path = path + ["cancel"]
        else:
            path = PATHS[status]

        # Durations in hours for each step, scaled by priority and category.
        speed = PRIORITY_SPEED[priority]
        durations = []
        for action in path:
            if action == "triage":
                hours = rng.uniform(0.5, 24) * speed
            elif action == "assign":
                hours = rng.uniform(1, 48) * speed
            elif action == "start":
                hours = rng.uniform(1, 72) * speed
            elif action == "resolve":
                hours = rng.uniform(2, 120) * speed * REPAIR_SPEED[category]
            elif action == "reopen":
                hours = rng.uniform(4, 13 * 24)
            elif action == "close":
                hours = rng.uniform(2, 168)
            else:  # cancel
                hours = rng.uniform(1, 72)
            durations.append(timedelta(hours=hours))
        # Squeeze the timeline if it would run past now (recent requests).
        room = (now - created_at) - timedelta(minutes=1)
        total = sum(durations, timedelta())
        if total > room:
            durations = [d * (room / total) for d in durations]

        req = MaintenanceRequest(
            unit=unit,
            created_by=creator,
            title=f"{category.title()} issue #{rng.randint(1000, 9999)}",
            description="Seeded for benchmarking.",
            category=category,
            requested_priority=priority,
            priority=priority,
            status=status,
            created_at=created_at,
            sla_due_at=created_at + timedelta(hours=settings.MAINTENANCE_SLA_HOURS[priority]),
        )
        events = [
            RequestEvent(
                actor=creator, from_status=None, to_status=S.SUBMITTED, created_at=created_at
            )
        ]
        current, when, contractor = S.SUBMITTED, created_at, None
        for action, duration in zip(path, durations, strict=True):
            when = when + duration
            if action in ("triage", "assign"):
                actor = manager
                if action == "assign":
                    contractor = rng.choices(self.contractors, weights=self.contractor_weights)[0]
                    req.assigned_to = contractor
            elif action in ("start", "resolve"):
                actor = contractor
                if action == "resolve":
                    req.resolved_at = when
            elif action == "reopen":
                actor = current_tenant
                req.resolved_at = None
                req.assigned_to = None
            elif action == "close":
                creator_still_here = self.active_at(creator_tenancy, when)
                actor = creator if creator_still_here and rng.random() < 0.7 else manager
                req.closed_at = when
            else:  # cancel
                creator_may = current == S.SUBMITTED and self.active_at(creator_tenancy, when)
                actor = creator if creator_may and rng.random() < 0.5 else manager
            to = TO_STATUS[action]
            events.append(
                RequestEvent(actor=actor, from_status=current, to_status=to, created_at=when)
            )
            current = to

        comments = []
        # Comments fall between creation and the last event (or now, if still open).
        window_end = when if status not in OPEN_STATUSES else now
        for _ in range(rng.choices(range(6), weights=[25, 25, 20, 15, 10, 5])[0]):
            at = created_at + (window_end - created_at) * rng.random()
            authors = [manager] + ([contractor] if contractor else [])
            if self.active_at(creator_tenancy, at):
                authors.append(creator)
            author = rng.choice(authors)
            internal = not author.is_tenant and rng.random() < 0.5
            comments.append(
                Comment(author=author, body="Seeded comment.", is_internal=internal, created_at=at)
            )
        return {"request": req, "events": events, "comments": comments}
