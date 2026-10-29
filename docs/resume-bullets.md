# Resume bullets: FixFlow

Format: **Action + Technology + Challenge + Verified result.** Every number links to the
file and section that proves it. There are no users, traffic or business impact to
claim (it's a portfolio project), so none are claimed.

1. **Built a REST API for rental maintenance workflows in Django REST Framework and PostgreSQL, with a single-table state machine and row-level locking (`SELECT … FOR UPDATE`) so concurrent status changes can't both succeed.**
   - The transition matrix test covers all [49 action × status pairs](../tests/test_transitions.py) (9 legal, 40 rejected with 409).
   - Real-thread race tests assert exactly one winner and one audit event. All [8 concurrency tests fail when the lock is removed](../docs/decisions.md#d23-how-performance-was-measured-and-optimized) (mutation check, re-run after the Phase 3 refactor; see also [the test file](../tests/test_concurrency.py)).

2. **Cut database queries per list page from 23 to 3 and list p50 latency about 3x (manager: 67.8 → 21.0 ms) on a 10,008-request dataset.**
   - Found and removed an N+1 query.
   - Added three indexes, each justified by `EXPLAIN (ANALYZE, BUFFERS)`. A fourth candidate was rejected because its gain was too small.
   - Evidence: [Baseline](benchmarks.md#baseline-2026-10-27-1937-utc), [Optimized](benchmarks.md#optimized-2026-10-27-1945-utc), [before/after table](benchmarks.md#baseline---optimized), [index evidence](benchmarks.md#index-decisions-explain-evidence) (manager page query 22.99 → 0.41 ms in Postgres). [`tests/test_query_counts.py`](../tests/test_query_counts.py) pins the counts.
   - *Honest scope:* measured in-process on one laptop with local Postgres (no network). p50 repeats within about 7–12% ([analysis](benchmarks.md#analysis-baseline-vs-optimized)). The query-count reduction is the hardware-independent part.

3. **Enforced object-level authorization (out-of-scope objects return 404 so existence never leaks), append-only audit logging with field-level edit diffs, and throttling with counters shared across gunicorn workers. Deployed on Render + Neon.**
   - 385 tests at 99.66% coverage in [GitHub Actions CI](https://github.com/MhBaigtw/fixflow/actions/runs/40951079197), which also boots the Docker image with gunicorn and smoke-tests it.
   - Visibility rules: [`tests/test_visibility.py`](../tests/test_visibility.py). Audit diffs: [`tests/test_edits.py`](../tests/test_edits.py). Throttling: [`tests/test_throttling.py`](../tests/test_throttling.py).
   - Live checks on the deployed service are recorded in [HANDOFF.md](../HANDOFF.md#live-deployment-verification):
     - the full lifecycle walkthrough;
     - the write throttle allowing 60 writes, then returning 429;
     - a proxy-depth bug found and fixed, where forged `X-Forwarded-For` headers had let logins skip the per-IP throttle.
