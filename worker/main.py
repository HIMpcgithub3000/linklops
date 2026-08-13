"""Analytics worker: drain the redirect queue, roll counts up by hour.

Runs as its own process, not a thread in the API. The redirect is the highest
traffic path in the product and the one with the tightest latency budget; a
rollup that shares its process shares its CPU, its memory and its restarts.

    python worker/main.py

Reads configuration through app.config like every other entrypoint, so a missing
or renamed key refuses to start here exactly as it does in the API. That is the
reason config validation lives at module scope rather than in a FastAPI startup
hook -- the worker has no lifespan hook to run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.redis_client import ANALYTICS_QUEUE, client  # noqa: E402

# Connects as the migration role, not the app role: this process writes to
# analytics on behalf of every tenant at once and has no request to bind a
# tenant from, so it cannot satisfy the RLS predicate the app role runs under.
# That is a deliberate privilege choice, not an oversight -- and it is exactly
# why the worker must never be given a query that takes a tenant id from data.
engine = create_engine(str(settings.MIGRATION_DATABASE_URL), pool_pre_ping=True)

# DATE_TRUNC on a timestamptz truncates in the *session's* time zone, so the
# bucket a click lands in depends on how the connection that happened to process
# it was configured. Anchoring to UTC explicitly removes the session from the
# calculation.
#
# The failure this prevents, measured rather than imagined: three clicks in one
# real hour, two processed by a worker session in UTC and one in Asia/Kolkata,
# produced two analytics rows -- buckets 17:00 and 17:30 -- for a single hour.
# The primary key is (link_id, timestamp_bucket), so ON CONFLICT never fired and
# the counts split silently instead of merging.
#
# What makes it genuinely nasty is that it is invisible for most of the world.
# America/New_York is a whole-hour offset, so truncating there and truncating in
# UTC name the same instant and the bug does not exist. It only appears for
# half- and quarter-hour zones -- India, Iran, Nepal, parts of Australia -- so a
# team testing in US or EU time zones cannot reproduce it, and the report from
# the field is the unfalsifiable-sounding "our hourly numbers are slightly wrong
# sometimes".
UPSERT = text(
    """
    INSERT INTO analytics (link_id, timestamp_bucket, count, last_accessed_at)
    VALUES (
        :link_id,
        DATE_TRUNC('hour', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
        1,
        clock_timestamp()
    )
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


def process_job(link_id: str) -> None:
    """Apply one click.

    count = analytics.count + 1 is computed by Postgres from the current row,
    not read into Python and written back. Two workers processing the same link
    in the same hour therefore serialise on the row lock and both increments
    land. A read-modify-write in application code would lose one of them, and
    would lose it silently -- undercounting is invisible in a way that a crash
    is not.
    """
    with engine.begin() as conn:
        conn.execute(UPSERT, {"link_id": link_id})


def main() -> int:
    print(f"worker: draining {ANALYTICS_QUEUE}", flush=True)
    while True:
        job = client.brpop(ANALYTICS_QUEUE, timeout=5)
        if not job:
            continue
        _, link_id = job
        try:
            process_job(link_id)
        except Exception as exc:  # noqa: BLE001
            # One bad job must not take the queue down. The job is dropped
            # rather than requeued: this is a rollup counter, so a lost click is
            # a rounding error, while an infinite retry of a poison message is a
            # worker that processes nothing else forever. Module 07 of System
            # Design is where a real dead-letter path belongs.
            print(f"worker: dropping job {link_id!r}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
