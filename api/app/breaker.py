"""A circuit breaker for the analytics enqueue on the redirect hot path.

Module 06 DECIDE chose a battle-tested library (pybreaker) over a hand-rolled
state machine, because a breaker's subtle states — the half-open trial's own
failure, concurrent requests racing a transition — are exactly where a custom
breaker written under pressure has bugs that fail silently.

**Why a breaker here specifically.** enqueue_click already swallows a Redis
failure so a redirect never fails for a missing analytics count (fail-open, the
right fallback). But without a breaker, *every* redirect during a Redis outage
still pays the cost of *attempting* the push — a connection attempt that fails,
which under a real outage can mean a socket timeout on the hottest path in the
product. That is the recommendation-engine incident in miniature: a non-critical
dependency slowing the critical path because nothing stops the doomed calls.

The breaker converts "attempt and fail, every request" into "fail fast, no
attempt": after `fail_max` consecutive failures it opens, and while open the
call is skipped entirely and the fallback (drop the click) runs immediately —
zero Redis contact. After `reset_timeout` it half-opens and lets one request
through as a trial; success closes it, failure reopens it. The redirect never
waits on Redis again once the breaker has tripped.

Thinly wrapped: the rest of the code calls `enqueue` and knows nothing about
pybreaker, so the dependency is swappable and the breaker's failure is contained
to this module.
"""

import logging

import pybreaker

from app import redis_client

log = logging.getLogger(__name__)

# fail_max=5: five consecutive failures is unambiguously "Redis is down", not a
# blip — a single transient failure should not trip the breaker and start
# dropping clicks the queue could still take. reset_timeout=30s: long enough not
# to hammer a recovering Redis with trial requests, short enough that analytics
# resumes within half a minute of recovery.
_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    name="analytics_enqueue",
)


class _StateLogger(pybreaker.CircuitBreakerListener):
    """State transitions are logged so a tripped breaker is visible in the logs,
    not a silent hole where analytics used to be."""

    def state_change(self, cb, old, new):
        # ERROR on open (analytics is now being dropped wholesale, someone should
        # know Redis is down), INFO on the recovery transitions.
        level = logging.ERROR if new.name == "open" else logging.INFO
        log.log(level, "analytics circuit %s -> %s", old.name, new.name)


_breaker.add_listener(_StateLogger())


def _push(payload: str) -> None:
    redis_client.client.lpush(redis_client.ANALYTICS_QUEUE, payload)


def enqueue(payload: str) -> None:
    """Push a click, or drop it fast when Redis is failing. Never raises.

    When the breaker is closed, this is an ordinary LPUSH. When it is open, the
    call is rejected instantly (CircuitBreakerError) without touching Redis, and
    the click is dropped with a warning — the same fail-open outcome as before,
    but at zero cost to the redirect instead of a failed connection attempt per
    request.
    """
    try:
        _breaker.call(_push, payload)
    except pybreaker.CircuitBreakerError:
        # Breaker is open: Redis is known-down, we did not even try. The redirect
        # continues; the click is lost, which is the accepted trade.
        log.warning("analytics enqueue skipped, circuit open: click not counted")
    except Exception as exc:  # noqa: BLE001
        # A real push failure while the breaker is still closed — swallowed, and
        # this failure is what the breaker counts toward opening.
        log.warning("analytics enqueue failed, click not counted: %s", type(exc).__name__)


def state() -> str:
    """Current breaker state, for tests and the metrics/verify path."""
    return _breaker.current_state
