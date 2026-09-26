"""Regression control: the four claims made for the redirect cache.

check_cache_scoping.py watches the *keys* -- that a cached read cannot escape
its tenant. This one watches the *behaviour*, which is the half that was
previously proven only by a live drill run by hand and written down in a log
file. A drill nobody re-runs is a claim, not a control.

Each claim is one this module actually asserts, and each has a failure that is
invisible from outside -- every scenario below returns a correct redirect even
when the property is broken, which is exactly why they need asserting:

  fill and hit     a miss populates the cache and the next read is served from
                   it. Broken: the cache silently never fills and every request
                   is a database read at cache-shaped cost.

  invalidation     dropping an entry actually removes it, so the next read is a
                   miss. Broken: a link keeps redirecting to its old
                   destination, and a disabled link keeps working, for a TTL.

  single flight    N concurrent misses on one cold key produce ONE load, not N.
                   Broken: the thundering herd -- faster in tests, and the
                   database absorbs the whole spike the first time a hot key
                   expires under load.

  fail soft        with Redis unreachable, a read reports a miss and a write and
                   an invalidation do not raise. Broken: an optional dependency
                   has quietly become a required one, and a Redis outage becomes
                   a redirect outage.

Requires a reachable Redis and refuses to pretend otherwise: without one,
fill_once() treats every caller as the lock winner, so the single-flight check
would pass for the wrong reason. It is skipped-as-failure rather than skipped.

No database and no server -- the loader is a counting stub, so what is under
test is this module's logic and not a round trip.

Exit 0 = all four hold. Exit 1 = at least one does not.
"""

import logging
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redis.exceptions import RedisError  # noqa: E402

from app import cache, redis_client  # noqa: E402

CONCURRENCY = 24
LOAD_SECONDS = 0.02  # Long enough that the losers are genuinely concurrent.


def _cold_code() -> str:
    """A code no other run has used, so a leftover entry cannot mask a failure."""
    return "chk" + uuid.uuid4().hex[:12]


def check_fill_and_hit() -> list[str]:
    code, link_id, url = _cold_code(), uuid.uuid4(), "https://example.com/fill"
    failures = []

    if cache.get_redirect_target(code) is not None:
        failures.append("  a cold key was already cached")

    cache.set_redirect_target(code, link_id, url)
    got = cache.get_redirect_target(code)
    if got is None:
        failures.append("  a set did not become a hit")
    elif got != (link_id, url):
        failures.append(f"  the cache returned a different value than was stored: {got!r}")

    ttl = redis_client.client.ttl(cache.key("redirect", code))
    ceiling = cache.REDIRECT_TTL_SECONDS + cache.TTL_JITTER_SECONDS
    if not 0 < ttl <= ceiling:
        # An entry with no expiry is the dangerous one: read-time expiry means
        # nothing will ever invalidate it, so it would outlive its link forever.
        failures.append(f"  TTL {ttl} is outside (0, {ceiling}] -- an entry that may never expire")

    cache.invalidate_redirect_target(code)
    return failures


def check_invalidation() -> list[str]:
    code, link_id = _cold_code(), uuid.uuid4()
    cache.set_redirect_target(code, link_id, "https://example.com/before")
    if cache.get_redirect_target(code) is None:
        return ["  could not seed the cache, so invalidation proves nothing"]

    cache.invalidate_redirect_target(code)
    if cache.get_redirect_target(code) is not None:
        return ["  the entry survived invalidation -- a stale destination would keep serving"]
    return []


def check_single_flight() -> list[str]:
    code, link_id, url = _cold_code(), uuid.uuid4(), "https://example.com/herd"
    loads = 0
    counter_lock = threading.Lock()
    ready = threading.Barrier(CONCURRENCY)

    def load():
        nonlocal loads
        with counter_lock:
            loads += 1
        # Stand in for the database read. The sleep is the point: without it the
        # winner finishes before the losers even start and the herd cannot form,
        # so a broken implementation would pass.
        time.sleep(LOAD_SECONDS)
        cache.set_redirect_target(code, link_id, url)
        return link_id, url

    def worker():
        ready.wait()
        return cache.fill_once(code, load)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = [f.result() for f in [pool.submit(worker) for _ in range(CONCURRENCY)]]

    cache.invalidate_redirect_target(code)

    failures = []
    if loads != 1:
        failures.append(
            f"  {CONCURRENCY} concurrent misses produced {loads} loads, not 1 -- "
            "the stampede is not collapsed"
        )
    if any(r != (link_id, url) for r in results):
        failures.append("  not every caller got the resolved value")
    return failures


def check_fail_soft() -> list[str]:
    """Redis unreachable: a read is a miss, a write and an invalidation are no-ops."""
    code = _cold_code()
    real = redis_client.client

    class Broken:
        def __getattr__(self, _name):
            def boom(*_args, **_kwargs):
                raise RedisError("simulated outage")
            return boom

    # The warnings this provokes are the correct behaviour under test, so they
    # are silenced rather than printed -- a control's output should be its
    # verdict, not the noise it deliberately caused.
    logging.getLogger("app.cache").setLevel(logging.CRITICAL)
    redis_client.client = Broken()
    failures = []
    try:
        if cache.get_redirect_target(code) is not None:
            failures.append("  a read during an outage did not report a miss")
        cache.set_redirect_target(code, uuid.uuid4(), "https://example.com/x")
        cache.invalidate_redirect_target(code)

        # The redirect must still resolve: fill_once has to fall through to the
        # loader when there is no Redis to coordinate with.
        marker = uuid.uuid4()
        got = cache.fill_once(code, lambda: (marker, "https://example.com/db"))
        if got != (marker, "https://example.com/db"):
            failures.append("  fill_once did not fall through to the database during an outage")
    except RedisError:
        failures.append(
            "  a Redis error escaped -- the cache is a required dependency, not optional"
        )
    finally:
        redis_client.client = real
        logging.getLogger("app.cache").setLevel(logging.NOTSET)
    return failures


CHECKS = [
    ("fill and hit", check_fill_and_hit),
    ("invalidation", check_invalidation),
    ("single flight", check_single_flight),
    ("fail soft", check_fail_soft),
]


def main() -> int:
    try:
        redis_client.client.ping()
    except RedisError as exc:
        print("check_cache_behaviour: FAIL")
        print(f"  Redis is unreachable ({type(exc).__name__}); without it the single-flight")
        print("  check would pass for the wrong reason, so this is a failure, not a skip.")
        return 1

    failures: list[str] = []
    for label, check in CHECKS:
        failures.extend(f"{line}  [{label}]" for line in check())

    if failures:
        print("check_cache_behaviour: FAIL")
        for failure in failures:
            print(failure)
        return 1

    print(
        f"cache behaviour ok: fill/hit, invalidation, single flight "
        f"({CONCURRENCY} concurrent misses -> 1 load), fail-soft under a Redis outage"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
