"""GET /api/stats/ with hand-built data and hand-computed medians."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.maintenance.models import MaintenanceRequest

from .conftest import assert_error
from .factories import PropertyFactory, UnitFactory

pytestmark = pytest.mark.django_db
S = MaintenanceRequest.Status
NOW = timezone.now()
T0 = NOW - timedelta(days=30)


@pytest.fixture
def data(wf):
    """All on wf's manager unless noted. Created at T0 + n days so date filters can slice."""
    second_unit = UnitFactory(property=PropertyFactory(manager=wf["manager"]))
    foreign_unit = UnitFactory(property=PropertyFactory(manager=wf["other_manager"]))

    def done(category, hours, status=S.CLOSED, unit=None, day=0):
        created = T0 + timedelta(days=day)
        return wf.make(
            status,
            unit=unit or wf["unit"],
            category=category,
            created_at=created,
            sla_due_at=created + timedelta(days=7),
            resolved_at=created + timedelta(hours=hours),
        )

    # plumbing: 2h, 4h, 10h -> median 4.0 (odd count: the middle value)
    done("plumbing", 2, day=1)
    done("plumbing", 4, status=S.RESOLVED, day=2)
    done("plumbing", 10, day=3)
    # electrical: 1h, 3h -> median 2.0 (even count: percentile_cont interpolates)
    done("electrical", 1, day=4)
    done("electrical", 3, status=S.RESOLVED, day=5)
    # appliance: 6h, on the manager's second property
    done("appliance", 6, unit=second_unit, day=6)
    # Excluded from medians: another manager's request, and non-resolved statuses.
    done("plumbing", 1000, unit=foreign_unit, day=7)
    wf.make(S.CANCELLED, category="heating", created_at=T0, sla_due_at=T0 + timedelta(days=1))

    past, future = NOW - timedelta(hours=1), NOW + timedelta(days=1)
    open_rows = [
        (S.SUBMITTED, past),  # overdue
        (S.SUBMITTED, future),
        (S.TRIAGED, future),
        (S.ASSIGNED, past),  # overdue
        (S.IN_PROGRESS, future),
    ]
    for status, due in open_rows:
        wf.make(status, category="heating", created_at=NOW - timedelta(days=2), sla_due_at=due)
    # Another manager's overdue open request must not count.
    wf.make(S.SUBMITTED, unit=foreign_unit, sla_due_at=past)
    return {"second_property": second_unit.property}


def get(client, **params):
    resp = client.get("/api/stats/", params)
    assert resp.status_code == 200, resp.content
    return resp.json()


def test_stats_hand_computed(wf, data, client_for):
    body = get(client_for(wf["manager"]))
    assert body == {
        "open_by_status": {"submitted": 2, "triaged": 1, "assigned": 1, "in_progress": 1},
        "overdue": 2,
        "median_hours_to_resolve": {
            "plumbing": 4.0,
            "electrical": 2.0,
            "heating": None,
            "appliance": 6.0,
            "pest": None,
            "structural": None,
            "other": None,
        },
    }


def test_other_manager_sees_only_their_own(wf, data, client_for):
    body = get(client_for(wf["other_manager"]))
    assert body["median_hours_to_resolve"]["plumbing"] == 1000.0
    assert body["open_by_status"]["submitted"] == 1
    assert body["overdue"] == 1


def test_property_filter(wf, data, client_for):
    client = client_for(wf["manager"])
    body = get(client, property=wf["property"].id)
    assert body["median_hours_to_resolve"]["appliance"] is None
    assert body["median_hours_to_resolve"]["plumbing"] == 4.0
    body = get(client, property=data["second_property"].id)
    assert body["median_hours_to_resolve"]["appliance"] == 6.0
    assert body["open_by_status"]["submitted"] == 0


def test_property_outside_scope_gives_zeros_not_their_data(wf, data, client_for):
    foreign = PropertyFactory(manager=wf["other_manager"])
    body = get(client_for(wf["manager"]), property=foreign.id)
    assert body["overdue"] == 0
    assert set(body["median_hours_to_resolve"].values()) == {None}


def test_created_window(wf, data, client_for):
    client = client_for(wf["manager"])
    # Days 1-3: the three plumbing requests only.
    body = get(
        client,
        created_after=(T0 + timedelta(days=1)).isoformat(),
        created_before=(T0 + timedelta(days=3)).isoformat(),
    )
    assert body["median_hours_to_resolve"]["plumbing"] == 4.0
    assert body["median_hours_to_resolve"]["electrical"] is None
    assert body["open_by_status"]["submitted"] == 0
    # From day 4: electrical (1h, 3h), appliance and all open requests.
    body = get(client, created_after=(T0 + timedelta(days=4)).isoformat())
    assert body["median_hours_to_resolve"]["plumbing"] is None
    assert body["median_hours_to_resolve"]["electrical"] == 2.0
    assert body["overdue"] == 2


@pytest.mark.parametrize("params", [{"created_after": "nope"}, {"property": "x"}])
def test_invalid_params_are_400(wf, client_for, params):
    resp = client_for(wf["manager"]).get("/api/stats/", params)
    assert_error(resp, 400, "validation_error")


@pytest.mark.parametrize("actor", ["creator", "contractor"])
def test_non_managers_get_403(wf, client_for, actor):
    assert_error(client_for(wf[actor]).get("/api/stats/"), 403, "permission_denied")


def test_anonymous_gets_401(anon):
    assert_error(anon.get("/api/stats/"), 401, "not_authenticated")


def test_empty_scope(client_for):
    from .factories import ManagerFactory

    body = get(client_for(ManagerFactory()))
    assert body["overdue"] == 0
    assert set(body["open_by_status"].values()) == {0}
    assert set(body["median_hours_to_resolve"].values()) == {None}
