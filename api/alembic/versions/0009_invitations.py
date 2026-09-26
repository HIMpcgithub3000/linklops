"""T3: invitations

Implements the three decisions settled in the Module 02 decision layer:

  1. Single-use, bound to an email AND a token. The token is the credential;
     the email is the constraint on who may redeem it. So a forwarded link is
     not sufficient on its own.
  2. Expiry is evaluated at READ TIME against now(). There is no status column
     a job flips, because a job leaves a window in which an expired invitation
     still resolves. Same rule already applied to link expiry.
  3. Revocation is a state transition, not a delete. The row is retained so the
     system can answer "who revoked this, and when".

The token is stored as a sha256 hash. The plaintext exists exactly once, in the
201 response and the email, and is never recoverable from the database -- same
treatment as api_keys, for the same reason: an invitation token is a bearer
credential that grants team membership.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Three independent nullable timestamps rather than one status column,
        # because the acceptance criterion is that an invitation can be expired,
        # revoked and accepted independently. A single status column cannot
        # express "revoked after acceptance", which is a real sequence.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name="ck_invitations_role"
        ),
        # revoked_by must be present exactly when revoked_at is. A nullable pair
        # that can disagree is two facts pretending to be one.
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by IS NULL)", name="ck_invitations_revoked_pair"
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by IS NULL)", name="ck_invitations_accepted_pair"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
    )

    # The token is the credential, so its hash must be unique cluster-wide --
    # not per team. Two teams minting the same token would make the token
    # ambiguous, and the accept endpoint looks up by token alone.
    op.create_unique_constraint("uq_invitations_token", "invitations", ["token_hash"])

    # Re-inviting the same email must return the SAME invitation rather than a
    # second one. Enforced in the database rather than by a check in the handler,
    # because check-then-insert loses to two concurrent requests. Partial: the
    # constraint applies only to invitations that are still live, so an email
    # that was revoked or has already accepted can legitimately be re-invited.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_invitations_live_email
        ON invitations (team_id, lower(email))
        WHERE revoked_at IS NULL AND accepted_at IS NULL;
        """
    )
    op.create_index("ix_invitations_team", "invitations", ["team_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE ON invitations TO upsk_app;")


def downgrade() -> None:
    op.drop_index("ix_invitations_team", table_name="invitations")
    op.execute("DROP INDEX IF EXISTS uq_invitations_live_email;")
    op.drop_constraint("uq_invitations_token", "invitations", type_="unique")
    op.drop_table("invitations")
