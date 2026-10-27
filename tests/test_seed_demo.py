from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import User
from apps.maintenance.models import Comment, MaintenanceRequest, RequestEvent
from apps.properties.models import Property, Tenancy, Unit

pytestmark = pytest.mark.django_db
MODELS = [User, Property, Unit, Tenancy, MaintenanceRequest, RequestEvent, Comment]
SECRET = "demo-secret-for-tests"


def counts():
    return {m.__name__: m.objects.count() for m in MODELS}


def seed():
    out = StringIO()
    call_command("seed_demo", stdout=out)
    return out.getvalue()


@pytest.fixture(autouse=True)
def demo_password(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", SECRET)


def test_seed_twice_creates_no_duplicates():
    seed()
    first = counts()
    seed()
    assert counts() == first
    assert first["User"] == 7
    assert User.objects.filter(role="manager").count() == 1
    assert User.objects.filter(role="contractor").count() == 2
    assert User.objects.filter(role="tenant").count() == 4
    assert first["Property"] == 2
    assert first["Comment"] == 2


def test_seed_covers_varied_states_with_audit_trails():
    seed()
    statuses = set(MaintenanceRequest.objects.values_list("status", flat=True))
    assert statuses == {
        "submitted",
        "triaged",
        "assigned",
        "in_progress",
        "resolved",
        "closed",
        "cancelled",
    }
    for req in MaintenanceRequest.objects.all():
        events = list(req.events.all())
        assert events[0].from_status is None
        assert events[-1].to_status == req.status


def test_seed_never_prints_password_and_users_can_log_in(anon):
    output = seed()
    assert SECRET not in output
    resp = anon.post("/api/auth/token/", {"email": "manager@fixflow.example", "password": SECRET})
    assert resp.status_code == 200


def test_seed_requires_demo_password(monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD")
    with pytest.raises(CommandError):
        seed()
    assert User.objects.count() == 0
