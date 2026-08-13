"""the database states which environment it is

Every configuration check in this service validates the *shape* of a value:
present, non-empty, parseable as a Postgres DSN. None of them can tell whether
DATABASE_URL points at the right database, because a production connection
string is exactly as well-formed as a staging one. That is the gap the module's
e-commerce incident lives in -- a staging service pointed at the production
database, a batch-delete test, 12,000 real customers.

A string cannot answer "am I the right database". Only the database can, so this
table is where it says so. One row, written at provision time, naming the
environment this database belongs to. The application compares it against its
own ENVIRONMENT at startup and refuses to run on a mismatch.

Why a table rather than the database name: names get copied. Restoring a
production dump into a database called linkops_staging produces a staging-named
database full of production data, and every name-based check passes. The row
travels with the data, because it IS data -- restore a production backup
anywhere and it still says production, which is precisely the behaviour wanted.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployment_identity",
        # A one-row table, enforced. `only_row` is always true and is the primary
        # key, so a second INSERT collides rather than quietly creating an
        # ambiguity about which row is authoritative.
        sa.Column("only_row", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("only_row", name="pk_deployment_identity"),
        sa.CheckConstraint("only_row", name="ck_deployment_identity_single"),
        sa.CheckConstraint(
            "environment IN ('development', 'staging', 'production')",
            name="ck_deployment_identity_env",
        ),
    )

    # Seeded from the environment the migration is run under, defaulting to
    # development. Deliberately not defaulted to production: a forgotten variable
    # should produce the least privileged answer, and a development database
    # mislabelled production would block a legitimate boot rather than permit an
    # illegitimate one -- but a production database mislabelled development would
    # permit exactly the write this table exists to prevent.
    op.execute(
        """
        INSERT INTO deployment_identity (only_row, environment)
        VALUES (true, COALESCE(NULLIF(current_setting('app.deploy_env', true), ''), 'development'))
        """
    )

    # Readable by the app role so the boot guard can check it; not writable, so a
    # compromised application cannot relabel the database it is running against.
    op.execute("GRANT SELECT ON deployment_identity TO upsk_app;")


def downgrade() -> None:
    op.drop_table("deployment_identity")
