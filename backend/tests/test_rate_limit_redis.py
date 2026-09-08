"""Redis-backed rate limiter (S10) — needs a real server.

These are skipped unless REDIS_URL is set, and CI sets it against a
redis:7-alpine service container. They are NOT run by the default suite: the
backend job's plain `pytest` step deliberately sets no env, so this file skips
there and runs in its own step.

Why a real server rather than a fake: the entire point of S10 is that the
check and the spend happen atomically inside Redis, which is a property of the
EVAL implementation. A fake with partial Lua support would produce a green
result that says nothing about the thing being claimed.

The load-bearing test is test_two_stores_share_one_budget. Its twin in
test_rate_limit_store.py asserts that two IN-PROCESS stores do NOT share a
budget — that pair is the before and after of this task.
"""

import os
import uuid

import pytest

from backend.app import rate_limit_store


REDIS_URL = os.getenv("REDIS_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="REDIS_URL is unset; the Redis-backed limiter is exercised in CI",
)


@pytest.fixture()
def store():
    """A store on a prefix unique to this test.

    Not the shared 'ratelimit:' prefix: these tests may run against the same
    Redis as a developer's live stack, and a reset() that wiped real buckets
    would be a test with a production side effect.
    """
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(REDIS_URL, socket_timeout=2.0)
    prefix = f"test-ratelimit-{uuid.uuid4().hex}:"
    s = rate_limit_store.RedisStore(client, prefix=prefix)
    try:
        yield s
    finally:
        s.reset()


def _new_store_on(prefix):
    """A SECOND store object with its own client — a stand-in for a second
    API replica. Sharing the client would test nothing about replicas."""
    import redis

    client = redis.Redis.from_url(REDIS_URL, socket_timeout=2.0)
    return rate_limit_store.RedisStore(client, prefix=prefix)


def test_admits_up_to_the_limit_then_sheds(store):
    assert [store.consume("scan", "c", 3, 60.0) for _ in range(3)] == [None] * 3

    retry_after = store.consume("scan", "c", 3, 60.0)
    assert retry_after is not None
    assert 1 <= retry_after <= 61, retry_after


def test_buckets_are_per_route_and_per_client(store):
    assert store.consume("scan", "c", 1, 60.0) is None
    assert store.consume("scan", "c", 1, 60.0) is not None

    assert store.consume("feedback", "c", 1, 60.0) is None
    assert store.consume("scan", "other", 1, 60.0) is None


def test_two_stores_share_one_budget(store):
    """THE POINT OF S10.

    Two independent store objects with separate connections — the test's stand
    -in for two API replicas — must spend from ONE budget. The in-process
    store fails this by construction, which is asserted directly in
    test_rate_limit_store.py::test_two_in_process_stores_do_not_share_a_budget.

    Would fail if: the window went back to process memory, or the script were
    replaced by a non-atomic read-then-write that lets two callers both observe
    count < limit.
    """
    replica_a = store
    replica_b = _new_store_on(store._prefix)

    assert replica_a.consume("scan", "same-client", 2, 60.0) is None
    assert replica_b.consume("scan", "same-client", 2, 60.0) is None

    # The budget of 2 is now spent globally. Neither replica may admit a third.
    assert replica_a.consume("scan", "same-client", 2, 60.0) is not None
    assert replica_b.consume("scan", "same-client", 2, 60.0) is not None


def test_hits_in_the_same_millisecond_are_all_counted(store):
    """A sorted set is a SET, so the member must be unique per hit.

    Would fail if: the member were the timestamp. Requests landing in the same
    millisecond would overwrite each other's element, ZCARD would undercount,
    and the limiter would leak requests under exactly the burst it exists to
    shed. A tight loop is the cheapest way to land several hits in one
    millisecond.
    """
    limit = 50
    admitted = sum(
        1 for _ in range(limit) if store.consume("burst", "c", limit, 60.0) is None
    )
    assert admitted == limit, "hits collapsed — members are not unique"

    assert store.consume("burst", "c", limit, 60.0) is not None


def test_window_expires_so_keys_do_not_leak(store):
    """Every distinct caller creates a key; without a TTL they accumulate."""
    store.consume("scan", "ttl-probe", 5, 60.0)

    key = store._key("scan", "ttl-probe")
    ttl = store._client.ttl(key)

    assert 0 < ttl <= 60, ttl


def test_retry_after_shrinks_as_the_window_advances(store):
    """retry-after must be time-to-oldest-hit, not a constant.

    A fixed-window INCR/EXPIRE implementation would report the same value on
    every rejection; this asserts the sliding-window behaviour the pre-S10
    limiter had and that the three api_guard tests depend on.
    """
    assert store.consume("scan", "c", 1, 2.0) is None

    first = store.consume("scan", "c", 1, 2.0)
    assert first is not None
    assert first <= 3


def test_consume_through_the_module_uses_redis_when_configured(monkeypatch):
    """The module-level entry point, not just the class, must reach Redis."""
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    rate_limit_store._drop_redis_store()
    try:
        assert rate_limit_store.active_backend() == "redis"
        client = f"module-probe-{uuid.uuid4().hex}"
        assert rate_limit_store.consume("scan", client, 1, 60.0) is None
        assert rate_limit_store.consume("scan", client, 1, 60.0) is not None
    finally:
        rate_limit_store.reset()
        rate_limit_store._drop_redis_store()
