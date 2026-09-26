"""api_keys: hashed credentials for the management plane

An API key IS a password that happens to be machine-generated, so it is stored
the way a password is stored: hashed, never in plaintext, shown once at creation
and never again.

Lookup is by `key_id`, a separate non-secret prefix carried in the header
alongside the secret. Without it, verifying a key means scanning every row and
hashing against each -- O(n) per request on the hot path of the admin API, and
it makes constant-time comparison pointless because the scan itself leaks timing.

Two keys per tenant are allowed on purpose: rotation with no downtime needs an
overlap window where the old and new key are both valid.

`last_used_at` exists so an unused key is visibly a candidate for revocation
rather than an invisible liability. A key that no longer expires on its own must
at least be observable.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("key_id", sa.String(16), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        # sha256 of the secret. Not bcrypt: this credential is high-entropy and
        # machine-generated, so it is not brute-forceable from the hash the way a
        # human password is, and a slow KDF on every admin request would be a
        # self-inflicted latency cost for no threat it defends against.
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        # Revocation is an UPDATE, and it takes effect on the very next request.
        # That immediacy is why this design was chosen over a bearer token: the
        # abuse case here is a tenant mass-producing phishing links on a shared
        # domain, and a fifteen-minute revocation delay is fifteen minutes of
        # damage to every other tenant.
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_keys_tenant", "api_keys", ["tenant_id"])

    # The app role may read keys to verify them and update last_used_at. It may
    # NOT insert or delete: minting and revoking credentials is a provisioning
    # operation, and a service that can mint its own keys can mint itself a
    # tenant. Same privilege split as the tenants table.
    op.execute("GRANT SELECT, UPDATE ON api_keys TO upsk_app;")


def downgrade() -> None:
    op.drop_index("ix_api_keys_tenant", table_name="api_keys")
    op.drop_table("api_keys")
