"""Retention enforcement: the purge deletes expired raw clicks and keeps the rollup.

Separated from the other integration tests because it cannot use the rolled-back
`db` fixture: the purge drops whole partitions with DDL, and DDL inside the
outer transaction the fixture holds would either deadlock on the fixture's locks
or escape the rollback. So this test owns its own connection, does its own
cleanup, and runs on the migration role -- the same role the purge itself uses,
because DROP TABLE is DDL the app role deliberately cannot perform.

It is still deterministic and still isolated: it creates a link and a click in a
throwaway partition it names, runs the real purge module, and asserts on that
link alone.
"""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config import settings

API_ROOT = Path(__file__).resolve().parent.parent


def _load_purge():
    path = API_ROOT / "scripts" / "purge_click_events.py"
    spec = importlib.util.spec_from_file_location("purge_click_events", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def owner_engine():
    return create_engine(str(settings.MIGRATION_DATABASE_URL))


def test_purge_drops_expired_clicks_and_keeps_the_rollup(owner_engine, monkeypatch):
    purge = _load_purge()
    tenant, link = uuid.uuid4(), uuid.uuid4()
    old_month = "click_events_1999_01"

    # A short retention so "old" is easy to construct, applied to the purge
    # module's view of settings without touching the process config.
    monkeypatch.setattr(purge.settings, "CLICK_RETENTION_DAYS", 30, raising=False)
    old_when = datetime(1999, 1, 15, tzinfo=UTC)

    with owner_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:t, 'retention')"), {"t": str(tenant)}
        )
        conn.execute(
            text(
                """
                INSERT INTO links (id, tenant_id, created_by, code, long_url)
                VALUES (:l, :t, :t, :c, 'https://example.com/retain')
                """
            ),
            {"l": str(link), "t": str(tenant), "c": f"ret{uuid.uuid4().hex[:9]}"},
        )
        # A partition entirely older than any real cutoff, so the purge drops it
        # wholesale -- the fast path the script is built around.
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {old_month} PARTITION OF click_events "
                "FOR VALUES FROM ('1999-01-01 00:00:00+00') TO ('1999-02-01 00:00:00+00')"
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO click_events (id, link_id, tenant_id, clicked_at, ip_hash)
                VALUES (:e, :l, :t, :w, :h)
                """
            ),
            {"e": str(uuid.uuid4()), "l": str(link), "t": str(tenant),
             "w": old_when, "h": "0" * 64},
        )
        # A rollup row for the same link. The purge must NOT touch this.
        conn.execute(
            text(
                """
                INSERT INTO analytics (link_id, timestamp_bucket, count, last_accessed_at)
                VALUES (:l, date_trunc('hour', :w), 5, :w)
                """
            ),
            {"l": str(link), "w": old_when},
        )

    try:
        with owner_engine.connect() as conn:
            before = conn.execute(
                text("SELECT count(*) FROM click_events WHERE link_id = :l"), {"l": str(link)}
            ).scalar()
        assert before == 1

        purge.main()

        with owner_engine.connect() as conn:
            after = conn.execute(
                text("SELECT count(*) FROM click_events WHERE link_id = :l"), {"l": str(link)}
            ).scalar()
            rollup = conn.execute(
                text("SELECT count FROM analytics WHERE link_id = :l"), {"l": str(link)}
            ).scalar()
            dropped = conn.execute(
                text("SELECT to_regclass(:p)"), {"p": old_month}
            ).scalar()

        assert after == 0, "expired click was not purged"
        assert rollup == 5, "the purge touched the aggregate rollup, which it must keep"
        assert dropped is None, "the expired partition was not dropped"
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {old_month}"))
            conn.execute(text("DELETE FROM analytics WHERE link_id = :l"), {"l": str(link)})
            conn.execute(text("DELETE FROM click_events WHERE link_id = :l"), {"l": str(link)})
            conn.execute(text("DELETE FROM links WHERE id = :l"), {"l": str(link)})
            conn.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant)})
