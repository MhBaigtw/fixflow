"""Racing transitions on one request.

Every race asserts: exactly one request succeeds, exactly one RequestEvent is
written, and the loser gets the response that is correct for the status the
winner committed. The loser is judged after the lock is released, so:
- if the winner's new status makes the loser's move illegal -> 409 invalid_transition
  (close vs reopen, double resolve, cancel winning over triage);
- if the move is still legal from the new status but not for the loser's role ->
  403 permission_denied (triage winning: the tenant's cancel from triaged is
  manager-only).
One test forces each ordering of triage vs cancel; another proves the loser
actually blocked on the row lock (its 409 took at least the winner's 0.5s hold).

These run with real threads, each on its own DB connection, against committed data
(transactional DB), so Postgres row locks are actually exercised. All of them fail
if select_for_update() is removed from apply_transition.
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


def assert_one_winner(responses, req, loser_by_outcome):
    """Exactly one 200 and one event. `loser_by_outcome` maps each possible final
    status to the (status_code, error_code) the losing request must get: the loser
    is judged against the state *after* the winner committed."""
    winners = [r for r in responses if r.status_code == 200]
    losers = [r for r in responses if r.status_code != 200]
    assert len(winners) == 1 and len(losers) == 1, [r.content for r in responses]
    req.refresh_from_db()
    assert req.status in loser_by_outcome
    status_code, error_code = loser_by_outcome[req.status]
    assert_error(losers[0], status_code, error_code)
    events = list(RequestEvent.objects.filter(request=req))
    assert len(events) == 1
    assert events[0].to_status == req.status


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("hold_lock", [False, True])
def test_close_vs_reopen_race(wf, client_for, request, hold_lock):
    """Whichever wins, the other move is illegal from the new status: always 409."""
    if hold_lock:
        request.getfixturevalue("slow_event_write")
    req = wf.make(S.RESOLVED)
    responses = race(
        [
            transition_call(client_for(wf["creator"]), req, "close"),
            transition_call(client_for(wf["roommate"]), req, "reopen"),
        ]
    )
    conflict = (409, "invalid_transition")
    assert_one_winner(responses, req, {S.CLOSED: conflict, S.TRIAGED: conflict})


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("hold_lock", [False, True])
def test_triage_vs_cancel_race(wf, client_for, request, hold_lock):
    """If cancel wins, triage is illegal from cancelled (409). If triage wins, the
    tenant's cancel is legal from triaged but manager-only, so it is a 403."""
    if hold_lock:
        request.getfixturevalue("slow_event_write")
    req = wf.make(S.SUBMITTED)
    responses = race(
        [
            transition_call(client_for(wf["manager"]), req, "triage"),
            transition_call(client_for(wf["creator"]), req, "cancel"),
        ]
    )
    assert_one_winner(
        responses,
        req,
        {
            S.CANCELLED: (409, "invalid_transition"),
            S.TRIAGED: (403, "permission_denied"),
        },
    )


def delayed(call, seconds):
    def run():
        time.sleep(seconds)
        return call()

    return run


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("first", ["manager", "creator"])
def test_triage_vs_cancel_each_ordering(wf, client_for, slow_event_write, first):
    """Force each winner in turn: the first caller takes the lock and holds it for
    0.5s; the second starts 0.1s later, blocks, then sees the committed status."""
    req = wf.make(S.SUBMITTED)
    triage = transition_call(client_for(wf["manager"]), req, "triage")
    cancel = transition_call(client_for(wf["creator"]), req, "cancel")
    if first == "manager":
        calls, expected_status, loser = [triage, delayed(cancel, 0.1)], S.TRIAGED, 403
    else:
        calls, expected_status, loser = [cancel, delayed(triage, 0.1)], S.CANCELLED, 409
    responses = race(calls)
    assert responses[0].status_code == 200
    assert responses[1].status_code == loser
    req.refresh_from_db()
    assert req.status == expected_status
    assert RequestEvent.objects.filter(request=req).count() == 1


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
    assert_one_winner(responses, req, {S.RESOLVED: (409, "invalid_transition")})


@pytest.mark.django_db(transaction=True)
def test_second_transaction_waits_for_lock(wf, client_for, slow_event_write):
    """The loser must block on the row lock (not read a stale status and fail fast)."""
    req = wf.make(S.RESOLVED)

    def timed(call):
        def run():
            started = time.monotonic()
            resp = call()
            return resp, time.monotonic() - started

        return run

    results = race(
        [
            timed(transition_call(client_for(wf["creator"]), req, "close")),
            timed(transition_call(client_for(wf["roommate"]), req, "reopen")),
        ]
    )
    assert sorted(r.status_code for r, _ in results) == [200, 409]
    # The loser never writes an event (so never sleeps itself); it can only take
    # >= 0.5s if it was blocked on the winner's row lock.
    loser_elapsed = next(elapsed for r, elapsed in results if r.status_code == 409)
    assert loser_elapsed >= 0.5
