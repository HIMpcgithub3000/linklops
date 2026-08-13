"""restore WITH CHECK on links.tenant_isolation after out-of-band drift

Found via pg_policies: links.tenant_isolation had with_check = true while qual
was intact, so reads stayed sealed and writes were wide open. Tenant B could
INSERT a row owned by tenant A and never see it again -- silent data injection
into another tenant's account, invisible to every read-side test.

The migration file was unmodified and alembic_version read 0002 the whole
time. Alembic records which revisions ran; it has no idea whether the schema
still matches them. A DDL statement run by hand is invisible to it forever.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    for table in ("links", "click_events"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING ({PREDICATE})
                WITH CHECK ({PREDICATE});
            """
        )


def downgrade() -> None:
    pass
