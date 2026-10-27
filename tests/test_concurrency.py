"""Racing transitions on one request: exactly one wins, the other gets 409.

These run with real threads, each on its own DB connection, against committed data
(transactional DB), so Postgres row locks are actually exercised.
"""

import threading
import time

import pytest
from django.db import connection

from apps.maintenance.models import MaintenanceRequest, RequestEvent
from apps.maintenance.services import transitions

from .conftest import assert_error

S = MaintenanceRequest.Status


def race(calls):
    """Run each zero-arg callable in its own thread, released together by a barrier."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i, call):
        try:
            barrier.wait(timeout=10)
            results[i] = call()
        except Exception as exc:  # surfaced to the test below
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a thread hung"
    for r in results:
        if isinstance(r, Exception):
            raise r
    return results


def transition_call(client, req, action, **extra):
    return lambda: client.post(
        f"/api/requests/{req.id}/transition/", {"action": action, **extra}, format="json"
    )


@pytest.fixture
def slow_event_write(monkeypatch):
    """Hold the row lock for a while before writing the event, so the second
    transaction is guaranteed to arrive while the first still holds the lock."""
    real = transitions.record_event

    def slow(*args, **kwargs):
        time.sleep(0.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(transitions, "record_event", slow)


def assert_one_winner(responses, req, winning_statuses):
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409], [r.content for r in responses]
    loser = next(r for r in responses if r.status_code == 409)
    assert_error(loser, 409, "invalid_transition")
    req.refresh_from_db()
    assert req.status in winning_statuses
    events = list(RequestEvent.objects.filter(request=req))
    assert len(events) == 1
    assert events[0].to_status == req.status


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("hold_lock", [False, True])
def test_triage_vs_cancel_race(wf, client_for, request, hold_lock):
    if hold_lock:
        request.getfixturevalue("slow_event_write")
    req = wf.make(S.SUBMITTED)
    responses = race(
        [
            transition_call(client_for(wf["manager"]), req, "triage"),
            transition_call(client_for(wf["creator"]), req, "cancel"),
        ]
    )
    assert_one_winner(responses, req, {S.TRIAGED, S.CANCELLED})


@pytest.mark.django_db(transaction=True)
def test_double_submit_of_same_transition(wf, client_for, slow_event_write):
    req = wf.make(S.IN_PROGRESS)
    contractor = client_for(wf["contractor"])
    responses = race(
        [
            transition_call(contractor, req, "resolve"),
            transition_call(contractor, req, "resolve"),
        ]
    )
    assert_one_winner(responses, req, {S.RESOLVED})


@pytest.mark.django_db(transaction=True)
def test_second_transaction_waits_for_lock(wf, client_for, slow_event_write):
    """The loser must block on the row lock (not read a stale status and fail fast)."""
    req = wf.make(S.SUBMITTED)

    def timed(call):
        def run():
            started = time.monotonic()
            resp = call()
            return resp, time.monotonic() - started

        return run

    results = race(
        [
            timed(transition_call(client_for(wf["manager"]), req, "triage")),
            timed(transition_call(client_for(wf["creator"]), req, "cancel")),
        ]
    )
    assert sorted(r.status_code for r, _ in results) == [200, 409]
    # The loser never writes an event (so never sleeps itself); it can only take
    # >= 0.5s if it was blocked on the winner's row lock.
    loser_elapsed = next(elapsed for r, elapsed in results if r.status_code == 409)
    assert loser_elapsed >= 0.5
