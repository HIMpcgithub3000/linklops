"""analytics rollup table for the LinkOps worker

The redirect path pushes a link id onto a Redis queue; the worker drains the
queue and rolls counts up into this table, one row per (link, hour).

Pre-aggregated by hour rather than one row per click, and that is the whole
point of the table existing alongside click_events. click_events is the
append-only fact log -- correct, unbounded, and expensive to ask "how many
clicks this hour" of. This is the answer to that question, maintained
incrementally. The two are allowed to disagree briefly (the queue has depth);
they are not allowed to disagree permanently, which is what makes the upsert
idempotency below load-bearing.

The PRIMARY KEY is (link_id, timestamp_bucket) because that is what makes the
worker's ON CONFLICT ... DO UPDATE possible at all. Without the constraint there
is no conflict target, so the worker would have to read-then-write, and two
workers processing the same link in the same hour would interleave and lose a
count. The constraint is not a nicety here, it is the concurrency control.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics",
        sa.Column("link_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp_bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("link_id", "timestamp_bucket", name="pk_analytics"),
    )
    # No FK to links(id). A click that arrives while a link is being deleted
    # would fail the worker's insert, and losing an analytics row is cheaper
    # than a worker that crash-loops on a race. The orphan is reconcilable; the
    # crash loop takes the whole queue down with it.

    # Reading a tenant's dashboard means "this tenant's links, recent buckets
    # first". link_id leads because it is the equality predicate.
    op.create_index("ix_analytics_bucket", "analytics", ["timestamp_bucket"])

    op.execute("GRANT SELECT, INSERT, UPDATE ON analytics TO upsk_app;")


def downgrade() -> None:
    op.drop_index("ix_analytics_bucket", table_name="analytics")
    op.drop_table("analytics")
