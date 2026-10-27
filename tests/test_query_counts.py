"""Query-count regression tests (Phase 3).

Counts include the JWT user lookup (1 query) that every authenticated request pays.
Before optimization the list cost 3 + 1 per row (23 queries for a 20-row page,
see docs/benchmarks.md, Baseline); it must now be constant in page size.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.maintenance.models import Comment, MaintenanceRequest, RequestEvent

pytestmark = pytest.mark.django_db
S = MaintenanceRequest.Status

LIST = 3  # auth user + COUNT(*) + page (request JOIN unit)
DETAIL = 2  # auth user + request JOIN unit
SUBLIST = 4  # auth user + request JOIN unit + COUNT(*) + page


def fill(wf, n):
    return [wf.make(S.ASSIGNED) for _ in range(n)]


@pytest.mark.parametrize("actor", ["manager", "creator", "contractor"])
@pytest.mark.parametrize("rows", [5, 20])
def test_list_query_count_is_constant_in_page_size(
    wf, client_for, django_assert_num_queries, actor, rows
):
    fill(wf, rows)
    client = client_for(wf[actor])
    with django_assert_num_queries(LIST):
        resp = client.get("/api/requests/?page_size=20")
    assert len(resp.json()["results"]) == rows


@pytest.mark.parametrize("query", ["overdue=true", "ordering=priority", "status=assigned"])
def test_filtered_list_query_count(wf, client_for, django_assert_num_queries, query):
    # Overdue rows, so every filter returns a non-empty page (an empty page skips
    # the page query entirely).
    past = timezone.now() - timedelta(days=1)
    for _ in range(20):
        wf.make(S.ASSIGNED, sla_due_at=past)
    client = client_for(wf["manager"])
    with django_assert_num_queries(LIST):
        assert len(client.get(f"/api/requests/?{query}").json()["results"]) == 20


@pytest.mark.parametrize("actor", ["manager", "creator", "contractor"])
def test_detail_query_count(wf, client_for, django_assert_num_queries, actor):
    req = fill(wf, 1)[0]
    client = client_for(wf[actor])
    with django_assert_num_queries(DETAIL):
        assert client.get(f"/api/requests/{req.id}/").status_code == 200


@pytest.mark.parametrize("n", [1, 15])
def test_events_query_count(wf, client_for, django_assert_num_queries, n):
    req = fill(wf, 1)[0]
    RequestEvent.objects.bulk_create(
        [RequestEvent(request=req, actor=wf["manager"], to_status=S.SUBMITTED) for _ in range(n)]
    )
    client = client_for(wf["manager"])
    with django_assert_num_queries(SUBLIST):
        assert len(client.get(f"/api/requests/{req.id}/events/").json()["results"]) == n


@pytest.mark.parametrize("actor", ["manager", "creator"])
@pytest.mark.parametrize("n", [1, 15])
def test_comments_query_count(wf, client_for, django_assert_num_queries, actor, n):
    req = fill(wf, 1)[0]
    Comment.objects.bulk_create(
        [Comment(request=req, author=wf["manager"], body="x") for _ in range(n)]
    )
    client = client_for(wf[actor])
    with django_assert_num_queries(SUBLIST):
        assert len(client.get(f"/api/requests/{req.id}/comments/").json()["results"]) == n


@pytest.mark.parametrize(
    "actor,action,status",
    [("manager", "triage", S.SUBMITTED), ("contractor", "start", S.ASSIGNED)],
)
def test_transition_query_budget(
    wf, client_for, django_assert_max_num_queries, actor, action, status
):
    """Lock + permission check must not re-fetch unit/property (joined with the lock)."""
    req = wf.make(status)
    client = client_for(wf[actor])
    # auth, scoped lookup, savepoint, locked SELECT (joins unit+property), UPDATE,
    # INSERT event, release savepoint.
    with django_assert_max_num_queries(7):
        resp = client.post(f"/api/requests/{req.id}/transition/", {"action": action})
    assert resp.status_code == 200
