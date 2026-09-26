"""Analytics worker: drain the redirect queue, store the click, roll counts up.

Runs as its own process, not a thread in the API. The redirect is the highest
traffic path in the product and the one with the tightest latency budget; a
rollup that shares its process shares its CPU, its memory and its restarts.

    python worker/main.py

Reads configuration through app.config like every other entrypoint, so a missing
or renamed key refuses to start here exactly as it does in the API. That is the
reason config validation lives at module scope rather than in a FastAPI startup
hook -- the worker has no lifespan hook to run.

Module 07 changed the delivery guarantee, and the ordering of that change is the
whole point. Previously this loop used BRPOP, which is at-most-once: the job
leaves the queue before it is applied, so a crash in between loses a click. The
bias was toward undercounting, and there were no retries, because a retry on
top of a non-idempotent `count = count + 1` would double-count -- the incident
this module describes.

So idempotency came first, and only then delivery:

  1. the write became idempotent   one statement, below, in which the rollup
                                   increment is fed by the rows the raw insert
                                   actually created. A replay conflicts on the
                                   click_events primary key, produces no rows,
                                   and therefore increments nothing.
  2. delivery became at-least-once BRPOPLPUSH parks the job on a processing
                                   list until it commits, so a dead worker
                                   leaves the job recoverable instead of gone.
  3. failures became bounded       retried with backoff, then dead-lettered,
                                   never dropped and never retried forever.

Doing (2) before (1) would have converted every lost click into a double-counted
one, which is the worse failure: an undercount is a known unknown, and an
overcount is the number the business acts on.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from redis.exceptions import RedisError  # noqa: E402
from redis.exceptions import TimeoutError as RedisTimeoutError  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.redis_client import (  # noqa: E402
    ANALYTICS_DEAD,
    ANALYTICS_PROCESSING,
    ANALYTICS_QUEUE,
    client,
)

# Connects as the migration role, not the app role: this process writes to
# analytics on behalf of every tenant at once and has no request to bind a
# tenant from, so it cannot satisfy the RLS predicate the app role runs under.
# That is a deliberate privilege choice, not an oversight -- and it is exactly
# why the worker must never be given a query that takes a tenant id from data.
# The tenant on every row below is read from `links`, never from the payload.
# A SEPARATE engine in a SEPARATE process from the API -- which is the third and
# strongest defence against the retry-storm cascade (Debugging Module 06, Bug
# #10). That cascade is: a job fails, the worker retries it in a tight loop, each
# retry grabs a connection from a pool SHARED with the API, the pool exhausts,
# and every API request then hangs waiting for a connection the worker will never
# return. The API goes down with no error, and the cause is twelve steps away in
# a different component.
#
# The bulkhead here is not "give the worker 3 of the API's 20 connections" -- it
# is that the worker has no access to the API's pool at all, because it is a
# different process (the multi_service deploy decision). Even a total meltdown of
# this pool cannot touch the API's. The small bounded pool below is the inner
# wall: the worker cannot itself open unbounded connections against Postgres, so
# a storm is capped at these few rather than limited only by the database's
# max_connections. connect_timeout keeps a wedged database from hanging the
# worker on checkout.
engine = create_engine(
    str(settings.MIGRATION_DATABASE_URL),
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=0,
    pool_timeout=5,
    connect_args={"connect_timeout": 5},
)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5

# One statement, and the shape is what makes a retry safe.
#
# The raw insert is idempotent by primary key: click_events is (id, clicked_at),
# both minted at enqueue time, so the same job applied twice conflicts and does
# nothing the second time. The rollup then reads FROM that insert's RETURNING --
# so on a replay there are no returned rows, the second INSERT has nothing to
# select, and `count` does not move. Idempotency is therefore a property of the
# statement rather than a promise the caller has to keep.
#
# Writing these as two statements would lose exactly that. The insert would be
# idempotent and the increment would not, and the gap between them would be a
# window where a crash leaves a click stored but uncounted.
#
# tenant_id comes from links, not from the payload. The payload crosses a queue
# and is therefore data, and a worker that takes a tenant id from data is one
# malformed job away from writing into the wrong tenant. Joining links also
# makes a click for a deleted link a no-op rather than an orphan row.
APPLY = text(
    """
    WITH stored AS (
        INSERT INTO click_events
            (id, link_id, tenant_id, clicked_at, user_agent, referrer, ip_hash)
        SELECT
            CAST(:event_id AS uuid), l.id, l.tenant_id,
            CAST(:clicked_at AS timestamptz), :user_agent, :referrer, :ip_hash
        FROM links l
        WHERE l.id = CAST(:link_id AS uuid)
        ON CONFLICT (id, clicked_at) DO NOTHING
        RETURNING link_id, clicked_at
    )
    INSERT INTO analytics (link_id, timestamp_bucket, count, last_accessed_at)
    SELECT
        link_id,
        -- DATE_TRUNC on a timestamptz truncates in the *session's* time zone, so
        -- the bucket a click lands in would otherwise depend on how the
        -- connection that happened to process it was configured. Anchoring to
        -- UTC removes the session from the calculation.
        --
        -- The failure this prevents, measured rather than imagined: three clicks
        -- in one real hour, two processed by a worker session in UTC and one in
        -- Asia/Kolkata, produced two analytics rows -- buckets 17:00 and 17:30 --
        -- for a single hour. The primary key is (link_id, timestamp_bucket), so
        -- ON CONFLICT never fired and the counts split silently instead of
        -- merging. It is invisible for most of the world: America/New_York is a
        -- whole-hour offset, so truncating there and in UTC name the same
        -- instant. It only appears for half- and quarter-hour zones -- India,
        -- Iran, Nepal, parts of Australia -- so a team testing in US or EU zones
        -- cannot reproduce it, and the field report is the unfalsifiable-sounding
        -- "our hourly numbers are slightly wrong sometimes".
        DATE_TRUNC('hour', clicked_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
        1,
        clock_timestamp()
    FROM stored
    ON CONFLICT (link_id, timestamp_bucket)
    DO UPDATE SET count = analytics.count + 1,
                  last_accessed_at = GREATEST(
                      analytics.last_accessed_at, clock_timestamp()
                  )
    """
)
# clock_timestamp(), not NOW(). NOW() is transaction_timestamp() -- frozen at
# BEGIN, so it records when the writing transaction *started*, not when this
# statement ran. Under contention those are different facts: the row lock decides
# the winner by commit order while NOW() decides the value by begin order, and
# nothing keeps the two in agreement. Measured deterministically: T1 begins, T2
# begins 0.5s later, T2 writes and commits, then T1 writes and commits last --
# leaving last_accessed_at 0.53 seconds stale, because the final writer was the
# transaction that had started earliest.
#
# GREATEST on top, because clock_timestamp() alone is only monotonic while the
# row lock serialises writers on one machine. It costs nothing and it makes the
# column's meaning enforceable rather than merely intended: last_accessed_at can
# never move backwards, whatever the commit order, whatever the clock skew
# between a primary and a replica, whatever a future caller does.
#
# The count was correct in both versions, which is what made this worth writing
# down. The field everyone checks was right, the field nobody checks was wrong,
# and there was no error, no 500 and no log line -- a "links not accessed in 24h"
# query would quietly include links accessed seconds ago.


def process_job(job: dict) -> None:
    """Apply one click. Safe to call twice with the same payload."""
    with engine.begin() as conn:
        conn.execute(
            APPLY,
            {
                "event_id": job["event_id"],
                "link_id": job["link_id"],
                "clicked_at": job["clicked_at"],
                "user_agent": job.get("user_agent"),
                "referrer": job.get("referrer"),
                "ip_hash": job.get("ip_hash"),
            },
        )


def retire(raw: str, reason: str) -> None:
    """Move a job off the processing list and onto the dead letter list.

    LREM before LPUSH, and count=1 rather than 0: removing every copy would also
    remove an identical job legitimately being processed by another worker.
    """
    client.lpush(ANALYTICS_DEAD, json.dumps({"reason": reason, "job": raw}))
    client.lrem(ANALYTICS_PROCESSING, 1, raw)
    print(f"worker: dead-lettered ({reason}): {raw[:200]}", file=sys.stderr, flush=True)


def handle(raw: str) -> None:
    try:
        job = json.loads(raw)
        required = ("event_id", "link_id", "clicked_at")
        if not all(job.get(field) for field in required):
            raise ValueError(f"payload missing one of {required}")
    except (ValueError, TypeError) as exc:
        # Malformed beyond interpretation. Retrying cannot help -- the payload
        # will parse exactly as badly next time -- so it goes straight to the
        # dead letter list rather than burning MAX_ATTEMPTS to reach the same
        # place. Distinguishing "cannot ever succeed" from "might succeed later"
        # is the difference between a retry policy and a delay policy.
        retire(raw, f"malformed: {type(exc).__name__}: {exc}")
        return

    try:
        process_job(job)
    except Exception as exc:  # noqa: BLE001
        attempts = int(job.get("attempts", 0)) + 1
        if attempts >= MAX_ATTEMPTS:
            retire(raw, f"{type(exc).__name__} after {attempts} attempts")
            return
        # Requeued with the attempt count incremented and event_id untouched --
        # the retry must be the *same* event or the idempotency key is a new one
        # every time and every retry becomes a fresh click.
        job["attempts"] = attempts
        client.lpush(ANALYTICS_QUEUE, json.dumps(job))
        client.lrem(ANALYTICS_PROCESSING, 1, raw)
        print(
            f"worker: retry {attempts}/{MAX_ATTEMPTS} for {job['event_id']}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        # Exponential, not linear: 0.5s, 1s, 2s ... rather than 0.5s, 1s, 1.5s.
        # The distinction barely shows at MAX_ATTEMPTS=3, but the reason it is
        # exponential is the retry-storm cascade (Bug #10): a fleet of jobs all
        # failing at once against a struggling database must spread their retries
        # apart faster than linearly, or the retry traffic itself is a synchronised
        # thundering herd that keeps the database from recovering. Linear backoff
        # slows the loop; exponential backoff actually lets the dependency breathe.
        time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempts - 1)))
        return

    # Only now is the job gone. Between BRPOPLPUSH and this LREM the job exists
    # on the processing list, so a worker killed mid-flight leaves it recoverable
    # rather than lost -- which is only survivable because applying it twice is
    # a no-op.
    client.lrem(ANALYTICS_PROCESSING, 1, raw)


def recover_orphans() -> int:
    """Return anything a previous worker died holding.

    Runs once at startup. Without it the processing list is a leak: jobs move
    onto it and, if the process that owned them never comes back, nothing ever
    moves them off. Safe to be indiscriminate here only because this deployment
    runs a single worker -- with several, this would need a per-worker
    processing list, or it would claw back jobs another worker is mid-way
    through. Written down because that is a real constraint, not a detail.
    """
    recovered = 0
    while client.rpoplpush(ANALYTICS_PROCESSING, ANALYTICS_QUEUE):
        recovered += 1
    if recovered:
        print(f"worker: recovered {recovered} orphaned job(s)", flush=True)
    return recovered


def main() -> int:
    print(f"worker: draining {ANALYTICS_QUEUE}", flush=True)
    backoff = 0.0
    connected = False

    while True:
        try:
            if not connected:
                # Orphans are recovered on every reconnect, not only at startup.
                # A dropped connection is exactly when a job can be left on the
                # processing list, so recovering only once at boot would leak
                # precisely the jobs this handles.
                recover_orphans()
                connected = True
                backoff = 0.0

            # BRPOPLPUSH, not BRPOP: the job is moved rather than removed, so it
            # is never in flight and nowhere at the same time.
            raw = client.brpoplpush(ANALYTICS_QUEUE, ANALYTICS_PROCESSING, timeout=5)
            if not raw:
                continue
            handle(raw)

        except RedisTimeoutError:
            # An idle poll expiring, NOT a broker failure -- and the two are only
            # distinguishable by exception type, which is why this branch exists
            # separately from the one below.
            #
            # redis-py 8.1 surfaces the blocking timeout on BRPOPLPUSH/BLMOVE as
            # a socket TimeoutError rather than returning None. BRPOP returns
            # None from the same situation, so moving from BRPOP to BRPOPLPUSH
            # silently changed an empty queue from a normal return into an
            # exception. Reproduced directly: blmove(timeout=5) raises
            # TimeoutError at 5.0s while brpop(timeout=3) returns None at 3.1s.
            #
            # Treating it as a broker outage, which is where this started, means
            # an idle worker logs an error every five seconds and backs off from
            # a queue that is merely empty -- alert noise that trains everyone to
            # ignore the log line that will matter.
            continue

        except RedisError as exc:
            # The broker going away must not kill the worker. This was found by
            # running it: a transient "Timeout reading from socket" on the
            # blocking read took the process down, and a dead worker is a queue
            # that grows without anyone being told -- the redirect keeps serving,
            # so nothing user-facing reports the problem.
            #
            # Reconnect and retry with capped exponential backoff rather than a
            # tight loop, which would turn a brief broker outage into a busy
            # spin against a server already in trouble.
            connected = False
            backoff = min(backoff * 2 or 0.5, 30.0)
            print(
                f"worker: broker unavailable ({type(exc).__name__}: {exc}); "
                f"reconnecting in {backoff:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(backoff)


if __name__ == "__main__":
    raise SystemExit(main())
