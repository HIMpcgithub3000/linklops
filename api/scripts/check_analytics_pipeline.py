"""Regression control: the analytics pipeline's five load-bearing claims.

Everything here failed silently in some form while this module was being built,
which is the argument for the file existing. None of these defects produces an
error a user or an operator would see: the redirect keeps returning 302, the API
keeps returning 200, and the only symptom is a number that is wrong or a queue
that does not drain.

  queue agreement   producer and worker name the same list. A mismatch is the
                    quietest failure in the set -- clicks accumulate on a list
                    nobody reads, and every other check still passes.

  payload shape     the enqueued event carries the fields the worker needs, and
                    carries an *IP hash* rather than an IP. A raw address on the
                    queue is personal data in a place nothing retains, audits or
                    purges.

  idempotency       applying the same job twice stores one click and increments
                    the rollup once. This is the property that makes at-least-
                    once delivery safe; without it, every retry is an overcount.

  retry bound       a job that cannot succeed is dead-lettered, not retried
                    forever. Measured while broken: attempt 16 and climbing,
                    dead-letter list never growing, and -- because backoff grows
                    with attempts -- a real click behind it delayed 10.1s.

  tenant isolation  the analytics RLS policy added in migration 0010 actually
                    refuses another tenant's rollup. check_tenant_coverage.py
                    proves a policy *exists*; nothing else proves it *works*,
                    and this one is an EXISTS subquery through links rather than
                    the canonical column predicate check_rls.py knows how to
                    compare.

Requires Postgres and Redis. Writes are confined to one throwaway link created
and deleted per run, and the queue keys it uses are its own.

Exit 0 = all five hold. Exit 1 = at least one does not.
"""

import importlib.util
import json
import sys
import uuid
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_ROOT))

from redis.exceptions import RedisError  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app import redis_client  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import clicks  # noqa: E402


def load_worker():
    """Import the real worker module, not a copy of its logic.

    A control that reimplements the statement it is checking proves only that
    two copies agree. worker/main.py lives outside the api package, so it is
    loaded by path.
    """
    path = API_ROOT.parent / "worker" / "main.py"
    spec = importlib.util.spec_from_file_location("analytics_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = load_worker()
engine = create_engine(str(settings.MIGRATION_DATABASE_URL))


def check_queue_agreement() -> list[str]:
    """Producer and worker must name the same list, by shared constant.

    Checked structurally rather than by comparing two values: both already
    import the same name, so equality is trivially true. What is worth
    forbidding is a *literal* queue name anywhere in the app or the worker,
    which is how the two drift apart in the first place.
    """
    failures = []
    literal = f'"{redis_client.ANALYTICS_QUEUE}"'
    for path in [*sorted((API_ROOT / "app").rglob("*.py")), API_ROOT.parent / "worker" / "main.py"]:
        if path.name == "redis_client.py":
            continue  # Where the constant is defined.
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if literal in line or f"'{redis_client.ANALYTICS_QUEUE}'" in line:
                failures.append(
                    f"  {path.name}:{number} hardcodes the queue name instead of "
                    "importing the constant"
                )
    return failures


def check_payload_shape() -> list[str]:
    """The enqueued event carries what the worker needs, and no raw address."""

    class FakeRequest:
        headers = {"user-agent": "ControlAgent/1.0", "referer": "https://example.test/x"}

    ip = "203.0.113.77"
    payload = json.loads(clicks.build_event(uuid.uuid4(), FakeRequest(), ip))

    failures = []
    for field in ("event_id", "link_id", "clicked_at", "ip_hash", "attempts"):
        if field not in payload:
            failures.append(f"  payload is missing {field!r}, which the worker requires")

    if ip in json.dumps(payload):
        failures.append("  the raw client IP is on the queue")
    if payload.get("ip_hash") == clicks.hashlib.sha256(ip.encode()).hexdigest():
        # The salt is the whole defence. Without it the stored value is a
        # reversible encoding of the address: 2^32 candidates is a lookup table,
        # not a search.
        failures.append("  ip_hash is an UNSALTED digest -- reversible, not anonymised")
    if payload.get("attempts") != 0:
        failures.append("  a fresh event does not start at attempt 0")

    # clicked_at must be an absolute instant, or the worker buckets it against
    # an assumed zone and partitions it into the wrong month.
    if "+" not in str(payload.get("clicked_at")) and "Z" not in str(payload.get("clicked_at")):
        failures.append("  clicked_at carries no UTC offset")
    return failures


def _seed_link(conn) -> tuple[uuid.UUID, uuid.UUID]:
    tenant, link = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text("INSERT INTO tenants (id, name) VALUES (:t, 'control') ON CONFLICT DO NOTHING"),
        {"t": str(tenant)},
    )
    conn.execute(
        text(
            """
            INSERT INTO links (id, tenant_id, created_by, code, long_url)
            VALUES (:i, :t, :t, :c, 'https://example.com/control')
            """
        ),
        {"i": str(link), "t": str(tenant), "c": "ctl" + uuid.uuid4().hex[:9]},
    )
    return tenant, link


def _counts(conn, link: uuid.UUID) -> tuple[int, int]:
    events = conn.execute(
        text("SELECT count(*) FROM click_events WHERE link_id = :l"), {"l": str(link)}
    ).scalar()
    rolled = conn.execute(
        text("SELECT COALESCE(SUM(count), 0) FROM analytics WHERE link_id = :l"), {"l": str(link)}
    ).scalar()
    return int(events), int(rolled)


def check_idempotency_and_isolation() -> list[str]:
    failures: list[str] = []
    with engine.begin() as conn:
        tenant, link = _seed_link(conn)

    job = json.loads(clicks.build_event(link, type("R", (), {"headers": {}})(), "198.51.100.9"))
    try:
        # Applied three times with the identical payload. Three, not two: two
        # would pass for an implementation that happens to alternate.
        for _ in range(3):
            worker.process_job(job)

        with engine.begin() as conn:
            events, rolled = _counts(conn, link)
        if events != 1:
            failures.append(f"  three identical jobs stored {events} click events, not 1")
        if rolled != 1:
            failures.append(
                f"  three identical jobs incremented the rollup to {rolled}, not 1 -- "
                "the increment is not gated on the raw insert"
            )

        # A different event for the same link must still count: idempotency must
        # not be achieved by deduplicating clicks that are genuinely distinct.
        other = json.loads(
            clicks.build_event(link, type("R", (), {"headers": {}})(), "198.51.100.9")
        )
        worker.process_job(other)
        with engine.begin() as conn:
            events, rolled = _counts(conn, link)
        if (events, rolled) != (2, 2):
            failures.append(
                f"  a genuinely distinct click was swallowed: events={events}, rollup={rolled}, "
                "expected 2 and 2"
            )

        # Tenant isolation, as the app role, against a DIFFERENT tenant.
        app_engine = create_engine(str(settings.DATABASE_URL))
        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(uuid.uuid4())}
            )
            leaked = conn.execute(
                text("SELECT COALESCE(SUM(count), 0) FROM analytics WHERE link_id = :l"),
                {"l": str(link)},
            ).scalar()
        if int(leaked or 0) != 0:
            failures.append(
                f"  another tenant read {leaked} clicks from the analytics rollup -- "
                "the migration 0010 policy is not scoping reads"
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM click_events WHERE link_id = :l"), {"l": str(link)})
            conn.execute(text("DELETE FROM analytics WHERE link_id = :l"), {"l": str(link)})
            conn.execute(text("DELETE FROM links WHERE id = :l"), {"l": str(link)})
            conn.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant)})
    return failures


def check_retry_bound() -> list[str]:
    """A job that can never succeed must reach the dead-letter list.

    Runs against control-only queue keys, swapped into the worker module for the
    duration. Two reasons, and the second was found the hard way: a control must
    not push test jobs onto the queue a production worker is draining, and if it
    does, a *running* worker races it for them -- the first version of this check
    failed intermittently because the live worker consumed the requeued job
    before the assertion could read it back.
    """
    failures = []
    if not isinstance(worker.MAX_ATTEMPTS, int) or worker.MAX_ATTEMPTS < 1:
        return [f"  MAX_ATTEMPTS is {worker.MAX_ATTEMPTS!r}, which is not a finite bound"]

    run = uuid.uuid4().hex[:8]
    saved = (worker.ANALYTICS_QUEUE, worker.ANALYTICS_PROCESSING, worker.ANALYTICS_DEAD)
    worker.ANALYTICS_QUEUE = f"check:{run}:queue"
    worker.ANALYTICS_PROCESSING = f"check:{run}:processing"
    worker.ANALYTICS_DEAD = f"check:{run}:dead"
    try:
        return _retry_bound_body(worker.ANALYTICS_QUEUE, worker.ANALYTICS_DEAD, failures)
    finally:
        worker.ANALYTICS_QUEUE, worker.ANALYTICS_PROCESSING, worker.ANALYTICS_DEAD = saved
        redis_client.client.delete(
            f"check:{run}:queue", f"check:{run}:processing", f"check:{run}:dead"
        )


def _retry_bound_body(queue: str, dead: str, failures: list[str]) -> list[str]:
    poison = json.dumps(
        {
            "event_id": str(uuid.uuid4()),
            "link_id": str(uuid.uuid4()),
            "clicked_at": "NOT-A-TIMESTAMP",
            "attempts": worker.MAX_ATTEMPTS - 1,
        }
    )
    before = redis_client.client.llen(dead)
    queued_before = redis_client.client.llen(queue)

    worker.handle(poison)

    after = redis_client.client.llen(dead)
    queued_after = redis_client.client.llen(queue)
    if after != before + 1:
        failures.append(
            "  a job on its final attempt was not dead-lettered -- it will be retried forever"
        )
    if queued_after != queued_before:
        failures.append("  a job on its final attempt was requeued as well as retired")


    # And a job with attempts left must be retried rather than discarded: a
    # bound that dead-letters on the first failure loses clicks to transient
    # faults, which is the opposite error and just as wrong.
    retryable = json.dumps(
        {
            "event_id": str(uuid.uuid4()),
            "link_id": str(uuid.uuid4()),
            "clicked_at": "NOT-A-TIMESTAMP",
            "attempts": 0,
        }
    )
    dead_before = redis_client.client.llen(dead)
    worker.handle(retryable)
    if redis_client.client.llen(dead) != dead_before:
        failures.append("  a job with attempts remaining was dead-lettered instead of retried")
    else:
        requeued = redis_client.client.lpop(queue)
        if requeued is None:
            failures.append("  a failing job with attempts remaining was dropped entirely")
        elif json.loads(requeued).get("attempts") != 1:
            failures.append("  a requeued job did not carry an incremented attempt count")
    return failures


CHECKS = [
    ("queue agreement", check_queue_agreement),
    ("payload shape", check_payload_shape),
    ("idempotency + tenant isolation", check_idempotency_and_isolation),
    ("retry bound", check_retry_bound),
]


def main() -> int:
    try:
        redis_client.client.ping()
    except RedisError as exc:
        print("check_analytics_pipeline: FAIL")
        print(f"  Redis is unreachable ({type(exc).__name__}); the retry-bound check needs it.")
        return 1

    failures: list[str] = []
    for label, check in CHECKS:
        failures.extend(f"{line}  [{label}]" for line in check())

    if failures:
        print("check_analytics_pipeline: FAIL")
        for failure in failures:
            print(failure)
        return 1

    print(
        "analytics pipeline ok: queue agreement, payload carries a salted hash, "
        "3 identical jobs -> 1 click, distinct jobs still counted, rollup scoped "
        f"to its tenant, retries bounded at {worker.MAX_ATTEMPTS} then dead-lettered"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
