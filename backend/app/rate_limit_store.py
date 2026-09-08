# ==========================================================
# File: rate_limit_store.py
# Purpose: the STORAGE behind the rate limiter (S10). api_guard.py keeps the
#          policy — which bucket, what limit, whether to send 429 — and this
#          module answers the single question "may this caller spend one unit?"
# ==========================================================
#
# Why this is a separate file when api_guard.py exists to hold the whole trust
# boundary in one readable place: the boundary is a policy statement, and this
# is a mechanism with two interchangeable implementations. Keeping the Lua
# script and the connection lifecycle next to the CORS origin parser would make
# api_guard harder to read, not easier. api_guard still owns every decision.
#
# ENV (read at CALL time, never at import time — the api_guard.py idiom):
#   REDIS_URL   If set, the shared Redis store is used and the limit is global
#               across API replicas. If UNSET the in-process store is used and
#               the limit is per-process, which is exactly as accurate on a
#               single-container deployment and needs no dependency running.
#
# The in-process store was the whole limiter before S10. It is kept, unchanged
# in behaviour, for two reasons: it is the correct implementation when there is
# one replica, and it is what a Redis outage degrades to.

import logging
import os
import random
import threading
import time

logger = logging.getLogger(__name__)


# ----------------------------------------------------------
# In-process store — a sliding window per (bucket, client)
# ----------------------------------------------------------

class InProcessStore:
    """Fixed-size sliding window held in this process's memory.

    Accurate for a single replica and wrong for several: two API containers
    each grant the full budget, so the effective limit is N x the configured
    one. That is the defect S10 exists to fix, and the reason this class is a
    fallback rather than the default once REDIS_URL is set.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._buckets = {}

    def consume(self, bucket, client, limit, window_seconds):
        now = time.monotonic()
        key = (bucket, client)

        with self._lock:
            hits = self._buckets.get(key, [])
            # Drop everything outside the trailing window.
            hits = [t for t in hits if now - t < window_seconds]

            if len(hits) >= limit:
                oldest = min(hits)
                retry_after = max(1, int(window_seconds - (now - oldest)) + 1)
                self._buckets[key] = hits
                return retry_after

            hits.append(now)
            self._buckets[key] = hits
            return None

    def reset(self):
        with self._lock:
            self._buckets.clear()


# ----------------------------------------------------------
# Redis store — the same window, shared across replicas
# ----------------------------------------------------------

# One EVAL, not a pipeline. A pipeline would read the count and write the new
# hit as two separate round trips, and two replicas interleaving those is
# precisely the race this task exists to remove — both would observe count <
# limit and both would be admitted. Redis runs a script to completion before
# serving anyone else, so the check and the spend cannot be split.
#
# Time comes from Redis's own TIME, never from the caller. time.monotonic() is
# process-local and has no shared origin, so scores written by two replicas
# would not be comparable — the window would be meaningless in exactly the
# deployment this is for. (TIME makes the script non-deterministic, which
# matters only on Redis < 5; effect replication has been the default since
# then, and the stack pins redis:7-alpine.)
#
# The member is unique per hit rather than the timestamp itself. A sorted set
# is a SET: two requests landing in the same millisecond with the timestamp as
# member would collapse into one element, and the limiter would silently
# undercount under exactly the burst it is meant to shed.
_CONSUME_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local member = ARGV[3]

local clock = redis.call('TIME')
local now_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)

if redis.call('ZCARD', key) >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local wait_ms = (tonumber(oldest[2]) + window_ms) - now_ms
  if wait_ms < 0 then
    wait_ms = 0
  end
  return math.floor(wait_ms / 1000) + 1
end

redis.call('ZADD', key, now_ms, member)
-- Expire the whole key one window after the newest hit. Without this, every
-- distinct client address that ever called leaks a key forever.
redis.call('PEXPIRE', key, window_ms)
return 0
"""

KEY_PREFIX = "ratelimit:"


class RedisStore:
    """The same sliding window, kept in Redis so every replica shares it."""

    def __init__(self, client, prefix=KEY_PREFIX):
        self._client = client
        self._prefix = prefix
        # register_script uses EVALSHA and falls back to EVAL on NOSCRIPT, so
        # the script body crosses the wire once per server rather than once per
        # request.
        self._script = client.register_script(_CONSUME_SCRIPT)

    def _key(self, bucket, client):
        return f"{self._prefix}{bucket}:{client}"

    def consume(self, bucket, client, limit, window_seconds):
        member = f"{time.time_ns()}-{random.getrandbits(32)}"
        retry_after = int(self._script(
            keys=[self._key(bucket, client)],
            args=[int(limit), int(window_seconds * 1000), member],
        ))
        # The script returns 0 for "admitted"; the caller's contract is None.
        return retry_after if retry_after > 0 else None

    def reset(self):
        """Drop every bucket. Used by tests; scoped to this module's prefix so
        it can never touch the Celery broker sharing the same server."""
        for key in self._client.scan_iter(match=f"{self._prefix}*", count=500):
            self._client.delete(key)


# ----------------------------------------------------------
# Selection, connection lifecycle, and the degraded path
# ----------------------------------------------------------

_fallback_store = InProcessStore()

# After a failure, stop trying Redis for this long. Without a cool-down every
# single request re-pays the connect timeout while Redis is down — measured at
# ~1s per call against a closed port, so a dead Redis would add a second of
# latency to every request and write one log line per request. The cool-down
# turns an outage into one probe every 30s.
_DEGRADED_COOLDOWN_SECONDS = 30.0

_state_lock = threading.Lock()
_redis_store = None
_redis_url = None
_degraded_logged = False
_degraded_until = 0.0


def _redis_errors():
    """The exception types that mean 'Redis is not answering'.

    Imported lazily and defensively: if the redis package is somehow absent,
    every call degrades to the in-process store instead of raising at import
    and taking the whole API down over a rate limiter.
    """
    try:
        from redis.exceptions import RedisError
        return (RedisError, OSError)
    except ImportError:
        return (OSError,)


def _build_redis_store(url):
    from redis import Redis

    client = Redis.from_url(
        url,
        # Bound every call. The default is no timeout at all, which would let
        # an unreachable Redis hang a request thread indefinitely — a worse
        # outage than the one the fallback exists to survive.
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
    )
    return RedisStore(client)


def _current_store():
    """The Redis store if REDIS_URL is set and usable, else None.

    Returns None without touching the network while a cool-down is running, so
    the caller degrades immediately instead of re-paying the connect timeout.
    """
    global _redis_store, _redis_url

    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None

    with _state_lock:
        if _redis_store is not None and _redis_url == url:
            return _redis_store
        if time.monotonic() < _degraded_until:
            return None
        try:
            store = _build_redis_store(url)
        except Exception as exc:  # noqa: BLE001 - never fail a request on this
            _enter_degraded_locked(exc)
            return None

        _redis_store = store
        _redis_url = url
        return _redis_store


def _enter_degraded_locked(exc):
    """Drop the client, start the cool-down, and log at most once per outage.

    Assumes _state_lock is held. _degraded_logged is deliberately NOT cleared
    here or by a successful reconnect attempt — only an actually successful
    Redis command clears it (see consume). Clearing it on reconnect would log
    once per cool-down for the whole outage, which is the flood this avoids.
    """
    global _redis_store, _redis_url, _degraded_logged, _degraded_until

    _redis_store = None
    _redis_url = None
    _degraded_until = time.monotonic() + _DEGRADED_COOLDOWN_SECONDS

    if not _degraded_logged:
        logger.warning(
            "Rate limiter could not reach Redis (%s); falling back to the "
            "in-process limiter. The limit is now per-replica.",
            exc,
        )
        _degraded_logged = True


def _drop_redis_store():
    global _redis_store, _redis_url, _degraded_until
    with _state_lock:
        _redis_store = None
        _redis_url = None
        _degraded_until = 0.0


def consume(bucket, client, limit, window_seconds):
    """Spend one unit for (bucket, client). Returns retry-after seconds if the
    budget is exhausted, or None if the request may proceed.

    A Redis failure degrades to the in-process store rather than failing open
    or failing closed. Failing open would delete the control during exactly the
    incident where load-shedding matters; failing closed would turn a broker
    hiccup into a total API outage. Degrading keeps a real limit — per-replica
    instead of global, which is the pre-S10 behaviour and was considered
    acceptable then.
    """
    global _degraded_logged

    store = _current_store()
    if store is not None:
        try:
            retry_after = store.consume(bucket, client, limit, window_seconds)
        except _redis_errors() as exc:
            with _state_lock:
                _enter_degraded_locked(exc)
        else:
            # A command that actually round-tripped is the only proof Redis is
            # back; only that re-arms the warning for the next outage.
            _degraded_logged = False
            return retry_after

    return _fallback_store.consume(bucket, client, limit, window_seconds)


def reset():
    """Clear every bucket in whichever store is live, plus the fallback.

    Both are cleared because a test may have degraded from one to the other
    mid-run, and a leftover hit in the store that was not active would make the
    next test flaky in a way that is very hard to read.
    """
    global _degraded_logged
    store = _current_store()
    if store is not None:
        try:
            store.reset()
        except _redis_errors():
            _drop_redis_store()
    _fallback_store.reset()
    _degraded_logged = False


def active_backend():
    """'redis' or 'in-process' — what the NEXT call would actually use.

    Exposed so /health can report it. An operator reading 'in-process' on a
    multi-replica deployment is looking at a silently degraded limiter, which
    is otherwise invisible.

    This PINGS rather than merely checking that a client object exists.
    Constructing a redis-py client performs no I/O — register_script only
    computes a SHA locally — so a store built against a dead server looks
    perfectly healthy until the first real command. Reporting 'redis' there
    would make /health assert exactly the thing an operator is checking for.
    """
    store = _current_store()
    if store is None:
        return "in-process"
    try:
        store._client.ping()
    except _redis_errors() as exc:
        with _state_lock:
            _enter_degraded_locked(exc)
        return "in-process"
    return "redis"
