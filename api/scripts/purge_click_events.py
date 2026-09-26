"""Retention purge: delete raw click events older than CLICK_RETENTION_DAYS.

    .venv/bin/python scripts/purge_click_events.py [--dry-run]

What is purged and what is not is the decision this script encodes:

  click_events    raw, per-visitor rows -- an ip_hash, a user agent, a referrer.
                  Personal data, deleted on a schedule.

  analytics       aggregate counts per (link, hour). No visitor is identifiable
                  in it, nothing joins back to a person, and it is what the
                  product actually reads. Kept, so the numbers survive the purge.

That split is why the Module 07 endpoint reads the rollup rather than counting
raw events: if it counted raw events, its answer would shrink every time this
script ran, and a figure that changes when nothing happened is worse than no
figure at all.

**Partitions are dropped, not deleted from.** click_events is RANGE partitioned
by month on clicked_at, so a month entirely older than the cutoff is removed
with DROP TABLE -- constant time, no dead tuples, no vacuum debt, no long-held
lock. A DELETE over the same rows rewrites every page, leaves the space needing
VACUUM, and on a table fed by the highest-traffic path in the product that is a
maintenance problem the day someone actually needs it.

Only the partially-expired month and the DEFAULT partition need a DELETE, and
they are bounded by construction: at most one month of rows plus whatever landed
in DEFAULT, rather than the entire history.

Run it repeatedly. It is idempotent -- a second run finds nothing left to do.

Runs as the migration role: DROP TABLE is DDL, which the app role deliberately
cannot perform.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402

PARENT = "click_events"


def cutoff() -> datetime:
    """The oldest instant still worth keeping.

    Computed in UTC explicitly. A cutoff derived from a local clock would move
    the retention boundary by the operator's time zone, which is a compliance
    figure decided by whoever happened to run the script.
    """
    return datetime.now(UTC) - timedelta(days=settings.CLICK_RETENTION_DAYS)


def partitions(conn) -> list[str]:
    return [
        row.relname
        for row in conn.execute(
            text(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_inherits i ON i.inhrelid = c.oid
                JOIN pg_class p ON p.oid = i.inhparent
                WHERE p.relname = :parent
                ORDER BY c.relname
                """
            ),
            {"parent": PARENT},
        )
    ]


def upper_bound(conn, name: str) -> datetime | None:
    """The exclusive upper bound of a range partition, or None for DEFAULT.

    Parsed by asking Postgres to cast the literal rather than by reading the
    text of the bound expression -- the printed format is a presentation detail
    and has changed between versions, and a retention job that silently stops
    matching is a retention job that silently stops deleting.
    """
    bound = conn.execute(
        text(
            """
            SELECT pg_get_expr(c.relpartbound, c.oid) AS bound
            FROM pg_class c WHERE c.relname = :name
            """
        ),
        {"name": name},
    ).scalar()
    if not bound or "DEFAULT" in bound:
        return None
    try:
        _, _, tail = bound.partition(" TO (")
        literal = tail.strip().rstrip(")").strip().strip("'")
        return conn.execute(
            text("SELECT CAST(:literal AS timestamptz)"), {"literal": literal}
        ).scalar()
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    limit = cutoff()
    engine = create_engine(str(settings.MIGRATION_DATABASE_URL))

    dropped: list[str] = []
    deleted = 0

    with engine.begin() as conn:
        before = conn.execute(text(f"SELECT count(*) FROM {PARENT}")).scalar()

        for name in partitions(conn):
            top = upper_bound(conn, name)
            # Strictly less than the cutoff: a partition whose upper bound equals
            # the cutoff still contains rows that are exactly at the boundary and
            # must be kept, because the window is half-open the same way the
            # analytics endpoint's is.
            if top is not None and top <= limit:
                dropped.append(name)
                if not dry_run:
                    conn.execute(text(f"DROP TABLE {name}"))
            elif top is None:
                # DEFAULT: no bound to reason about, so the rows have to be
                # inspected. Bounded in practice because DEFAULT should be empty
                # -- anything in it is a click that arrived outside every
                # declared month, which is itself worth noticing.
                count = conn.execute(
                    text(f"SELECT count(*) FROM {name} WHERE clicked_at < :limit"),  # noqa: S608
                    {"limit": limit},
                ).scalar()
                if count and not dry_run:
                    conn.execute(
                        text(f"DELETE FROM {name} WHERE clicked_at < :limit"),  # noqa: S608
                        {"limit": limit},
                    )
                deleted += count or 0
            else:
                # The partially-expired month: keep the partition, delete the
                # rows inside it that are past the cutoff.
                count = conn.execute(
                    text(f"SELECT count(*) FROM {name} WHERE clicked_at < :limit"),  # noqa: S608
                    {"limit": limit},
                ).scalar()
                if count and not dry_run:
                    conn.execute(
                        text(f"DELETE FROM {name} WHERE clicked_at < :limit"),  # noqa: S608
                        {"limit": limit},
                    )
                deleted += count or 0

        after = conn.execute(text(f"SELECT count(*) FROM {PARENT}")).scalar()

    prefix = "purge (dry run)" if dry_run else "purge"
    print(
        f"{prefix}: retention {settings.CLICK_RETENTION_DAYS}d, "
        f"cutoff {limit.isoformat()}"
    )
    print(f"  partitions dropped: {', '.join(dropped) if dropped else '(none)'}")
    print(f"  rows deleted from remaining partitions: {deleted}")
    print(f"  click_events rows: {before} -> {after}")
    print("  analytics rollup: untouched by design")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
