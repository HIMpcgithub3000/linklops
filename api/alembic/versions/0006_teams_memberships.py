"""T1: teams and team_memberships

Team Collaboration schema. Lives in this migration tree rather than in the
TaskFlow starter because the starter has no persistence at all -- three empty
JavaScript arrays -- and standing up a second database to avoid mixing would
cost more than it buys. Recorded so nobody later infers that src/ owns this.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ROLES = ("owner", "admin", "member")


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "team_memberships",
        sa.Column("team_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # The unique constraint IS the acceptance criterion for this task: a
        # duplicate membership must fail rather than create a second row. Enforced
        # by the database because "the code checks first" is a race, not a rule --
        # check-then-insert loses to two concurrent requests, which I measured
        # failing at a concurrency of two in the Debugging skill.
        sa.PrimaryKeyConstraint("team_id", "user_id", name="pk_team_memberships"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name="ck_team_memberships_role"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_team_memberships_user", "team_memberships", ["user_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON teams, team_memberships TO upsk_app;")


def downgrade() -> None:
    op.drop_index("ix_team_memberships_user", table_name="team_memberships")
    op.drop_table("team_memberships")
    op.drop_table("teams")
