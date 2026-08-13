"""make the RLS predicate safe for a reset transaction-local GUC

0001's policy read:

    tenant_id = current_setting('app.tenant_id', true)::uuid

which is correct exactly once per connection. `set_config(..., is_local => true)`
reverts at commit to the value the setting had before the transaction -- and
for a custom GUC that was never set at session level, that revert leaves it
*defined as the empty string* rather than undefined. Verified:

    fresh session      -> NULL      -- current_setting(..., true) returns NULL
    after a local set  -> ''        -- the GUC now exists, empty

So the first unscoped query on a connection matched zero rows correctly, and
every subsequent one raised `invalid input syntax for type uuid: ""`. Under a
connection pool that makes the policy's behaviour depend on which connection
you were handed and what it did previously -- passes in a test with one
connection, fails in production under load.

Both outcomes deny access, so this was never a data leak. It was a 500 where
a clean empty result belonged, on the path the public redirect uses.

Fix: nullif(..., '') collapses both the undefined and the reset-to-empty case
to NULL, and `tenant_id = NULL` is NULL, which excludes the row. Fails closed
either way, without raising.

New migration rather than an edit to 0001: 0001 has already been applied, and
changing an applied migration makes the file and the database disagree while
both claim to be at the same revision -- the drift class this project exists
to stamp out.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PREDICATE_SAFE = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
PREDICATE_OLD = "tenant_id = current_setting('app.tenant_id', true)::uuid"


def _recreate(predicate: str) -> None:
    for table in ("links", "click_events"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING ({predicate})
                WITH CHECK ({predicate});
            """
        )


def upgrade() -> None:
    _recreate(PREDICATE_SAFE)


def downgrade() -> None:
    _recreate(PREDICATE_OLD)
