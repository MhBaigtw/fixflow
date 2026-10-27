"""seed_bulk produces data that satisfies every model and workflow rule."""

from datetime import timedelta
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.models import User
from apps.maintenance.models import Comment, MaintenanceRequest, RequestEvent
from apps.maintenance.services.transitions import TRANSITIONS
from apps.properties.models import Property, Tenancy

pytestmark = pytest.mark.django_db
S = MaintenanceRequest.Status
SMALL = ["--requests", "400", "--managers", "2", "--properties", "4", "--units", "24"]
SMALL += ["--contractors", "5", "--force"]
LEGAL = {(t.from_status, t.to_status) for t in TRANSITIONS}


def seed(*args):
    out = StringIO()
    call_command("seed_bulk", *args, stdout=out)
    return out.getvalue()


@pytest.fixture(scope="module")
def seeded(django_db_setup, django_db_blocker):
    """Seed once for the module (it's the slow part), then clean up."""
    with django_db_blocker.unblock():
        output = seed("--seed", "7", *SMALL)
        yield output
        from django.core.management import call_command as cc

        cc("flush", "--no-input", verbosity=0)


def test_refuses_without_debug_or_force():
    with pytest.raises(CommandError, match="DEBUG"):
        seed("--requests", "1")


def test_reports_counts_and_runtime(seeded):
    assert "Seed 7 created in" in seeded
    assert "requests     +400" in seeded
    assert "Database totals:" in seeded


def test_idempotent_per_seed(seeded):
    before = {m: m.objects.count() for m in (User, MaintenanceRequest, RequestEvent, Comment)}
    output = seed("--seed", "7", *SMALL)
    assert "already present" in output
    assert {m: m.objects.count() for m in before} == before


def test_event_chains_are_legal_and_match_status(seeded):
    now = timezone.now()
    events_by_request = {}
    for e in RequestEvent.objects.order_by("created_at", "id"):
        events_by_request.setdefault(e.request_id, []).append(e)

    requests = list(MaintenanceRequest.objects.all())
    assert len(requests) == 400
    for req in requests:
        chain = events_by_request[req.id]
        assert (chain[0].from_status, chain[0].to_status) == (None, S.SUBMITTED)
        assert chain[0].actor_id == req.created_by_id
        assert chain[0].created_at == req.created_at
        for prev, nxt in zip(chain, chain[1:], strict=False):
            assert nxt.from_status == prev.to_status
            assert (nxt.from_status, nxt.to_status) in LEGAL
            assert prev.created_at <= nxt.created_at <= now
        assert chain[-1].to_status == req.status


def test_workflow_fields_consistent_with_status(seeded):
    sla = settings.MAINTENANCE_SLA_HOURS
    for req in MaintenanceRequest.objects.all():
        assert req.requested_priority == req.priority
        assert req.sla_due_at - req.created_at == timedelta(hours=sla[req.priority])
        assert (req.resolved_at is not None) == (req.status in (S.RESOLVED, S.CLOSED))
        assert (req.closed_at is not None) == (req.status == S.CLOSED)
        if req.status in (S.ASSIGNED, S.IN_PROGRESS, S.RESOLVED, S.CLOSED):
            assert req.assigned_to is not None and req.assigned_to.is_contractor
        if req.status in (S.SUBMITTED, S.TRIAGED):
            assert req.assigned_to is None


def test_actors_have_the_right_roles(seeded):
    for e in RequestEvent.objects.exclude(from_status=None).select_related(
        "actor", "request__unit__property"
    ):
        manager_id = e.request.unit.property.manager_id
        if e.to_status in (S.TRIAGED, S.ASSIGNED) and e.from_status != S.RESOLVED:
            assert e.actor_id == manager_id
        elif e.to_status in (S.IN_PROGRESS, S.RESOLVED):
            assert e.actor.is_contractor
        elif e.from_status == S.RESOLVED and e.to_status == S.TRIAGED:  # reopen
            assert e.actor.is_tenant
        elif e.to_status == S.CLOSED:
            assert e.actor_id == manager_id or e.actor.is_tenant


def test_creators_held_a_tenancy_when_they_reported(seeded):
    for req in MaintenanceRequest.objects.all():
        day = req.created_at.date()
        assert (
            Tenancy.objects.filter(
                unit_id=req.unit_id, tenant_id=req.created_by_id, start_date__lte=day
            )
            .exclude(end_date__lte=day)
            .exists()
        )


def test_tenants_never_post_internal_comments(seeded):
    assert Comment.objects.exists()
    assert not Comment.objects.filter(is_internal=True, author__role="tenant").exists()
    for c in Comment.objects.select_related("request"):
        assert c.created_at >= c.request.created_at


def test_some_tenancies_ended_and_some_requests_overdue(seeded):
    assert Tenancy.objects.filter(end_date__isnull=False).exists()
    statuses = set(MaintenanceRequest.objects.values_list("status", flat=True))
    assert statuses == set(S.values)
    assert Property.objects.filter(name="Bulk 7 Property 01").exists()
