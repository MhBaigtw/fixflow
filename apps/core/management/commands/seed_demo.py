"""Idempotent demo data. All users get the password from DEMO_PASSWORD (never printed).

Requests are driven through the real services, so each one has a genuine audit trail.
"""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.maintenance.models import MaintenanceRequest
from apps.maintenance.services.comments import add_comment
from apps.maintenance.services.requests import create_request
from apps.maintenance.services.transitions import apply_transition
from apps.properties.models import Property, Tenancy, Unit

DOMAIN = "fixflow.example"

USERS = [
    ("manager", User.Role.MANAGER, "Morgan", "Reyes"),
    ("plumber", User.Role.CONTRACTOR, "Pat", "Okafor"),
    ("electrician", User.Role.CONTRACTOR, "Eli", "Novak"),
    ("alice", User.Role.TENANT, "Alice", "Chen"),
    ("bob", User.Role.TENANT, "Bob", "Diaz"),
    ("carol", User.Role.TENANT, "Carol", "Singh"),
    ("dave", User.Role.TENANT, "Dave", "Kim"),
]

PROPERTIES = [
    ("Maple Court", "12 Maple Street", ["1A", "1B", "2A"]),
    ("Harbor View", "400 Harbor Road", ["101", "102"]),
]

# (tenant, property, unit). Alice and Bob are roommates.
TENANCIES = [
    ("alice", "Maple Court", "1A"),
    ("bob", "Maple Court", "1A"),
    ("carol", "Maple Court", "1B"),
    ("dave", "Harbor View", "101"),
]

# (title, creator, property, unit, category, priority, [(actor, action, contractor)])
REQUESTS = [
    ("Kitchen sink leaking", "alice", "Maple Court", "1A", "plumbing", "urgent", []),
    (
        "Bedroom outlet sparks",
        "bob",
        "Maple Court",
        "1A",
        "electrical",
        "emergency",
        [("manager", "triage", None)],
    ),
    (
        "Radiator not heating",
        "carol",
        "Maple Court",
        "1B",
        "heating",
        "urgent",
        [("manager", "triage", None), ("manager", "assign", "plumber")],
    ),
    (
        "Dishwasher won't drain",
        "dave",
        "Harbor View",
        "101",
        "appliance",
        "routine",
        [
            ("manager", "triage", None),
            ("manager", "assign", "plumber"),
            ("plumber", "start", None),
        ],
    ),
    (
        "Bathroom fan dead",
        "carol",
        "Maple Court",
        "1B",
        "electrical",
        "routine",
        [
            ("manager", "triage", None),
            ("manager", "assign", "electrician"),
            ("electrician", "start", None),
            ("electrician", "resolve", None),
        ],
    ),
    (
        "Cracked window pane",
        "alice",
        "Maple Court",
        "1A",
        "structural",
        "routine",
        [
            ("manager", "triage", None),
            ("manager", "assign", "plumber"),
            ("plumber", "start", None),
            ("plumber", "resolve", None),
            ("alice", "close", None),
        ],
    ),
    (
        "Hallway light flickers",
        "dave",
        "Harbor View",
        "101",
        "electrical",
        "routine",
        [("dave", "cancel", None)],
    ),
]


# title -> [(author, body, is_internal)]
COMMENTS = {
    "Radiator not heating": [
        ("carol", "It's getting cold at night, any ETA?", False),
        ("manager", "Plumber quoted high; check the warranty first.", True),
    ],
}


def email(key):
    return f"{key}@{DOMAIN}"


class Command(BaseCommand):
    help = "Create or refresh demo users, properties, tenancies and maintenance requests."

    def handle(self, *args, **options):
        password = os.environ.get("DEMO_PASSWORD")
        if not password:
            raise CommandError("Set DEMO_PASSWORD in the environment (or .env) first.")

        created = {"users": 0, "properties": 0, "units": 0, "tenancies": 0, "requests": 0}
        with transaction.atomic():
            users = {}
            for key, role, first, last in USERS:
                user, was_created = User.objects.get_or_create(
                    email=email(key),
                    defaults={"role": role, "first_name": first, "last_name": last},
                )
                user.set_password(password)
                user.save(update_fields=["password"])
                users[key] = user
                created["users"] += was_created

            units = {}
            for name, address, labels in PROPERTIES:
                prop, was_created = Property.objects.get_or_create(
                    name=name, manager=users["manager"], defaults={"address": address}
                )
                created["properties"] += was_created
                for label in labels:
                    unit, was_created = Unit.objects.get_or_create(property=prop, label=label)
                    units[(name, label)] = unit
                    created["units"] += was_created

            for tenant, prop_name, label in TENANCIES:
                _, was_created = Tenancy.objects.get_or_create(
                    unit=units[(prop_name, label)],
                    tenant=users[tenant],
                    end_date=None,
                    defaults={"start_date": "2026-01-01"},
                )
                created["tenancies"] += was_created

            for title, creator, prop_name, label, category, priority, steps in REQUESTS:
                unit = units[(prop_name, label)]
                if MaintenanceRequest.objects.filter(unit=unit, title=title).exists():
                    continue
                req = create_request(
                    tenant=users[creator],
                    unit=unit,
                    title=title,
                    description=f"Demo request: {title.lower()}.",
                    category=category,
                    priority=priority,
                )
                for actor, action, contractor in steps:
                    apply_transition(
                        request_id=req.pk,
                        actor=users[actor],
                        action=action,
                        contractor_id=users[contractor].pk if contractor else None,
                    )
                for author, body, is_internal in COMMENTS.get(title, []):
                    add_comment(
                        request=req, author=users[author], body=body, is_internal=is_internal
                    )
                created["requests"] += 1

        summary = ", ".join(f"{n} {k}" for k, n in created.items())
        self.stdout.write(self.style.SUCCESS(f"Demo data ready (newly created: {summary})."))
        self.stdout.write(
            "Log in as any of: "
            + ", ".join(email(k) for k, *_ in USERS)
            + " with the password from DEMO_PASSWORD."
        )
