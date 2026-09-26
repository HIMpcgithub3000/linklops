"""M7: put the analytics rollup inside the tenant boundary

`analytics` was the one table an authenticated caller could read that nothing
scoped. It holds no tenant_id -- it is keyed (link_id, timestamp_bucket) -- so
row-level security had nothing to attach to, and the app role holds SELECT on it
directly. A handler that reads it without joining `links` returns every tenant's
click counts, and it returns them as a well-formed 200.

That was tolerable only while nothing read the table. Module 07 adds the
endpoint that does, so it stops being tolerable here.

Two changes, and the first is what makes the second detectable rather than
merely correct:

  the foreign key   analytics.link_id -> links.id, ON DELETE CASCADE. It was
                    missing entirely: the rollup carried link ids that nothing
                    guaranteed existed, so a deleted link left counts behind
                    forever with no owner. It also means check_tenant_coverage.py
                    can *see* this table -- that control finds tables scoped
                    through a foreign key, and with no FK to follow it had
                    classified `analytics` as not tenant-scoped at all. A silent
                    false negative on the one table this module exposes.

  the policy        scoped through links rather than through a column of its
                    own. Denormalising tenant_id onto the rollup would be the
                    faster predicate, but it would also create a second place
                    the truth is written, and a rollup row whose tenant_id
                    disagrees with its link's is a cross-tenant read that looks
                    perfectly consistent from inside the table. One source of
                    truth, one join.

FORCE, like every other policy here, so the table owner is subject to it too.
The worker connects as a superuser and is therefore unaffected -- it writes on
behalf of every tenant at once and has no request to bind a tenant from, which
is the same deliberate privilege split already documented in worker/main.py.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

POLICY = "tenant_isolation"


def upgrade() -> None:
    # Orphans first: the FK cannot be added while rows point at links that no
    # longer exist. Deleting them is right rather than lossy -- a count with no
    # link is unreadable through the API by construction, since every read path
    # goes through links.
    op.execute(
        """
        DELETE FROM analytics a
        WHERE NOT EXISTS (SELECT 1 FROM links l WHERE l.id = a.link_id)
        """
    )
    op.execute(
        """
        ALTER TABLE analytics
        ADD CONSTRAINT fk_analytics_link
        FOREIGN KEY (link_id) REFERENCES links (id) ON DELETE CASCADE
        """
    )

    op.execute("ALTER TABLE analytics ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics FORCE ROW LEVEL SECURITY")

    # USING and WITH CHECK both, and identical. USING alone scopes what can be
    # read; without WITH CHECK the app role could still INSERT or UPDATE a row
    # against another tenant's link -- writing outside the boundary it cannot
    # read across, which is the drift already found and fixed once on links.
    predicate = """
        EXISTS (
            SELECT 1 FROM links l
            WHERE l.id = analytics.link_id
              AND l.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        )
    """
    op.execute(
        f"""
        CREATE POLICY {POLICY} ON analytics
        USING ({predicate})
        WITH CHECK ({predicate})
        """
    )

    # The join the policy performs has to be cheap, or every analytics read pays
    # a sequential scan of links. links.id is the primary key, so the lookup is
    # already an index hit -- this index is for the other direction, the rollup
    # read filtered by bucket that Module 07's endpoint issues.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_analytics_link_bucket "
        "ON analytics (link_id, timestamp_bucket)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_analytics_link_bucket")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON analytics")
    op.execute("ALTER TABLE analytics NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics DROP CONSTRAINT IF EXISTS fk_analytics_link")
