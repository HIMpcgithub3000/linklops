"""M7 (AI-Aug review, finding F2): row-level security for the teams tables

`teams`, `team_memberships` and `invitations` were the three tenant-scoped tables
the app role could read with **no** RLS — flagged by check_tenant_coverage since
they were built, and confirmed by the system-level review as the one place tenant
isolation rested entirely on application code (`require_role`). That is the
"security in context" gap: `require_role` is correct in isolation, but any query
that forgets it, or any injection that reaches these tables, crosses tenants with
no database backstop. The links plane has two walls (RLS + handler); this gives
the teams plane its missing second wall.

Scoping:

  teams              directly, by its own tenant_id column — the same predicate
                     as links and click_events.
  team_memberships   through the FK to teams. It has no tenant_id of its own, and
                     denormalising one would create a second source of truth that
                     could disagree with the team's. One join instead.
  invitations        through the FK to teams, same reasoning.

USING *and* WITH CHECK on every policy, identical, so a write cannot land a row
in another tenant any more than a read can see one — the drift that was found and
fixed once on links. FORCE so the table owner is subject too; the migration role
(superuser) is unaffected, which is correct because no request-bound flow runs as
it.

The FK-scoped subqueries read `teams`, which now also has RLS — consistent rather
than recursive, since the teams policy does not reference the other two tables.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

POLICY = "tenant_isolation"
TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _enable(table: str, predicate: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY} ON {table} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def _fk_predicate(table: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM teams t "
        f"WHERE t.id = {table}.team_id AND t.tenant_id = {TENANT})"
    )


def upgrade() -> None:
    _enable("teams", f"tenant_id = {TENANT}")
    _enable("team_memberships", _fk_predicate("team_memberships"))
    _enable("invitations", _fk_predicate("invitations"))


def downgrade() -> None:
    for table in ("invitations", "team_memberships", "teams"):
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
