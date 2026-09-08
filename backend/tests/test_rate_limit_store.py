"""Rate-limit storage tests that need no Redis (S10).

Everything here runs on any machine. The Redis-backed half of S10 — the Lua
script, and the property that two replicas share one budget — needs a real
server and lives in test_rate_limit_redis.py, which CI runs against a service
container.

The most important test in this file is
test_two_in_process_stores_do_not_share_a_budget (named rather than placed,
because "the last one" stops being true the moment anyone appends). It asserts
that two in-process stores do NOT share a budget: that is the S10 defect stated
as an executable fact, and its twin in the Redis file asserts the opposite. A
fixture that has never been watched failing against the pre-fix behaviour proves
nothing, and this pair is how that gets watched without a Redis to hand.
"""

import logging
import time

import pytest

from backend.app import api_guard, rate_limit_store


UNREACHABLE = "redis://127.0.0.1:1/0"


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    """Reset the module's cached client and buckets around every test.

    The store caches its Redis client in a module global keyed on REDIS_URL, so
    without this a test that degrades the client would leak that state into the
    next one — the kind of cross-test coupling that reads as a flaky suite
    rather than as the missing teardown it is.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    rate_limit_store._drop_redis_store()
    rate_limit_store._fallback_store.reset()
    rate_limit_store._degraded_logged = False
    yield
    rate_limit_store._drop_redis_store()
    rate_limit_store._fallback_store.reset()
    rate_limit_store._degraded_logged = False


# ----------------------------------------------------------
# api_guard delegates storage but keeps the policy
# ----------------------------------------------------------

def test_check_rate_limit_delegates_to_the_store(monkeypatch):
    """api_guard must ask the store, not keep its own buckets.

    Would fail if: the Redis store were added but never wired, leaving the
    process-local dict as the real limiter — S10 shipped in name only.
    """
    seen = {}

    def fake_consume(bucket, client, limit, window_seconds):
        seen.update(bucket=bucket, client=client, limit=limit,
                    window_seconds=window_seconds)
        return 7

    monkeypatch.setattr(rate_limit_store, "consume", fake_consume)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "42")

    assert api_guard.check_rate_limit("scan", "1.2.3.4") == 7
    assert seen == {"bucket": "scan", "client": "1.2.3.4",
                    "limit": 42, "window_seconds": 60.0}


def test_reset_rate_limiter_delegates_to_the_store(monkeypatch):
    called = []
    monkeypatch.setattr(rate_limit_store, "reset", lambda: called.append(True))

    api_guard.reset_rate_limiter()

    assert called == [True]


def test_limit_and_window_are_the_policy_api_guard_still_owns(monkeypatch):
    """The store is told the limit; it does not read the env itself."""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "not-a-number")
    assert api_guard.rate_limit_per_minute() == api_guard.DEFAULT_RATE_LIMIT_PER_MINUTE


# ----------------------------------------------------------
# The degraded path: Redis configured but not answering
# ----------------------------------------------------------

def test_unreachable_redis_falls_back_and_still_limits(monkeypatch):
    """A Redis outage must not delete the control.

    Would fail if: the limiter failed open on connection error, restoring the
    unlimited-clone surface during exactly the incident where shedding load
    matters most.
    """
    monkeypatch.setenv("REDIS_URL", UNREACHABLE)

    assert rate_limit_store.consume("scan", "c1", 2, 60.0) is None
    assert rate_limit_store.consume("scan", "c1", 2, 60.0) is None

    retry_after = rate_limit_store.consume("scan", "c1", 2, 60.0)
    assert retry_after is not None, "unreachable Redis silently removed the limit"
    assert 1 <= retry_after <= 61


def test_unreachable_redis_does_not_fail_the_request(monkeypatch):
    """Degrade, never fail closed. A broker hiccup must not 429 everyone."""
    monkeypatch.setenv("REDIS_URL", UNREACHABLE)
    assert rate_limit_store.consume("feedback", "c1", 60, 60.0) is None


def test_degraded_fallback_is_logged_once(monkeypatch, caplog):
    """An invisible degradation is the failure mode worth guarding.

    Once, not per request: a limiter that logs on every call turns a Redis
    outage into a log flood, which is its own incident.
    """
    monkeypatch.setenv("REDIS_URL", UNREACHABLE)

    with caplog.at_level(logging.WARNING, logger=rate_limit_store.__name__):
        for _ in range(5):
            rate_limit_store.consume("scan", "c1", 60, 60.0)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "per-replica" in warnings[0].getMessage()


def test_active_backend_without_redis_url():
    assert rate_limit_store.active_backend() == "in-process"


def test_active_backend_reports_in_process_when_redis_is_unreachable(monkeypatch):
    """/health must not claim 'redis' against a dead server.

    Would fail if: active_backend only checked that a client object exists.
    Building a redis-py client performs no I/O, so a client pointed at nothing
    looks healthy until the first real command.
    """
    monkeypatch.setenv("REDIS_URL", UNREACHABLE)
    assert rate_limit_store.active_backend() == "in-process"


# ----------------------------------------------------------
# In-process store behaviour (unchanged from pre-S10)
# ----------------------------------------------------------

def test_in_process_store_admits_up_to_the_limit_then_sheds():
    store = rate_limit_store.InProcessStore()

    assert [store.consume("scan", "c", 3, 60.0) for _ in range(3)] == [None] * 3

    retry_after = store.consume("scan", "c", 3, 60.0)
    assert retry_after is not None
    assert 1 <= retry_after <= 61


def test_in_process_buckets_are_independent():
    store = rate_limit_store.InProcessStore()

    assert store.consume("scan", "c", 1, 60.0) is None
    assert store.consume("scan", "c", 1, 60.0) is not None
    # Different route, and different caller: each its own budget.
    assert store.consume("feedback", "c", 1, 60.0) is None
    assert store.consume("scan", "other", 1, 60.0) is None


def test_in_process_reset_clears_every_bucket():
    store = rate_limit_store.InProcessStore()
    assert store.consume("scan", "c", 1, 60.0) is None
    assert store.consume("scan", "c", 1, 60.0) is not None

    store.reset()

    assert store.consume("scan", "c", 1, 60.0) is None


def test_two_in_process_stores_do_not_share_a_budget():
    """THE S10 DEFECT, stated as a fact.

    Two API replicas each hold their own dict, so a limit of 1 admits one
    request per replica — N times the configured budget. This test passes
    today and must keep passing: it is documenting why the Redis store exists,
    not asserting desired behaviour. Its twin,
    test_redis_stores_share_one_budget_across_instances, asserts that two
    stores sharing a Redis do NOT behave this way.
    """
    replica_a = rate_limit_store.InProcessStore()
    replica_b = rate_limit_store.InProcessStore()

    assert replica_a.consume("scan", "same-client", 1, 60.0) is None
    assert replica_a.consume("scan", "same-client", 1, 60.0) is not None

    # Budget already spent globally, yet the second replica admits it anyway.
    assert replica_b.consume("scan", "same-client", 1, 60.0) is None


def test_in_process_store_does_not_leak_a_key_per_departed_client():
    """One-shot callers must not accumulate a dict entry for the process life.

    The Redis store expires each key one window after its newest hit
    (PEXPIRE). The in-process store had no equivalent, so a client that
    called once and never returned kept its bucket forever and a public
    deployment grew one entry per distinct source address until the container
    ran out of memory. This is the default store when REDIS_URL is unset AND
    the store a Redis outage degrades to, so it is reachable both ways.

    The empty-list case is NOT the leak and never occurs: consume() stores
    hits non-empty on both paths -- it appends before storing when admitting,
    and the reject path requires len(hits) >= limit >= 1. Nothing ever
    revisits a departed client's key, so the fix has to be a sweep rather
    than a delete-when-empty.

    Real time with a tiny window rather than a patched clock: monkeypatching
    time.monotonic would freeze it process-wide for the duration of the test.
    """
    window = 0.05
    store = rate_limit_store.InProcessStore()

    for i in range(200):
        assert store.consume("scan", f"one-shot-{i}", 60, window) is None
    assert len(store._buckets) == 200

    # Every one of those windows has now fully expired.
    time.sleep(window + 0.02)
    store.consume("scan", "a-later-caller", 60, window)

    assert len(store._buckets) == 1, (
        f"{len(store._buckets)} buckets survived a full window with no "
        "traffic; departed clients are never pruned"
    )


def test_the_sweep_is_selective_not_a_blanket_clear():
    """A sweep must drop only buckets whose every hit has aged out.

    Dropping a live one would silently hand a caller that had already spent
    its budget a fresh window -- a limiter that forgets is worse than one
    that leaks. This forces a real sweep and asserts both directions at
    once, so a blanket clear fails here even though it would satisfy the
    leak test above.
    """
    window = 0.3
    store = rate_limit_store.InProcessStore()

    # Calls once and never returns: must be gone after the sweep.
    store.consume("scan", "departed", 5, window)

    store.consume("scan", "steady", 5, window)
    time.sleep(0.2)
    # Refreshes its bucket, so it is still inside the window at sweep time.
    store.consume("scan", "steady", 5, window)
    time.sleep(0.15)

    # Any call after a full window has elapsed is what drives the sweep.
    store.consume("scan", "trigger", 5, window)

    assert ("scan", "departed") not in store._buckets, (
        "the sweep did not run, so this test proves nothing about selectivity"
    )
    assert ("scan", "steady") in store._buckets, (
        "the sweep dropped a bucket that was still inside its window, "
        "handing that caller a fresh budget"
    )


# ----------------------------------------------------------
# /health probing — reporting must not mutate what it reports on
# ----------------------------------------------------------

class _CountingClient:
    """Minimal stand-in for a redis-py client that counts PINGs."""

    def __init__(self, fail=False):
        self.pings = 0
        self._fail = fail

    def ping(self):
        self.pings += 1
        if self._fail:
            raise OSError("connection refused")
        return True

    def register_script(self, script):
        return lambda keys, args: 0


def _install_client(monkeypatch, client):
    monkeypatch.setenv("REDIS_URL", "redis://installed-by-test:6379/0")
    monkeypatch.setattr(
        rate_limit_store,
        "_build_redis_store",
        lambda url: rate_limit_store.RedisStore(client),
    )
    rate_limit_store._drop_redis_store()


def test_a_failing_health_probe_does_not_demote_the_limiter(monkeypatch):
    """/health reports on the limiter; it must not change it.

    active_backend() used to call _enter_degraded_locked on a failed PING, so
    one ping exceeding the 1s socket_timeout under load dropped the whole
    container to the per-replica window for 30s -- at exactly the moment load
    was high. /health is in PUBLIC_PATHS and is not itself rate limited, so
    the trigger is reachable unauthenticated. consume() owns the degraded
    transition; a read-only report must not.
    """
    _install_client(monkeypatch, _CountingClient(fail=True))

    assert rate_limit_store.active_backend() == "in-process"

    assert rate_limit_store._degraded_until == 0.0, (
        "a health probe started a cool-down; reporting mutated the limiter"
    )


def test_health_probes_are_bounded_however_often_health_is_called(monkeypatch):
    """The probe must not be one blocking round trip per /health call.

    health() is sync, so it runs in the anyio threadpool. Against a slow Redis
    an unauthenticated caller looping GET /health would otherwise hold a
    worker thread for the full socket timeout on every request.
    """
    client = _CountingClient()
    _install_client(monkeypatch, client)

    for _ in range(10):
        assert rate_limit_store.active_backend() == "redis"

    assert client.pings == 1, (
        f"{client.pings} PINGs for 10 /health calls; the probe is unbounded"
    )
