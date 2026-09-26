"""narrow the app role's access to api_keys to one SECURITY DEFINER function

Migration 0007 granted the app role SELECT on api_keys so it could verify a
credential. That let it read every tenant's key_id, tenant_id and secret_hash
with no tenant bound -- privilege excess, not a live leak, since no route exposes
the table. It is the gap that turns one injection or one careless future endpoint
into a cross-tenant credential dump.

Row-level security cannot fix this one, and that is the interesting part.
api_keys is read BEFORE any tenant is bound, because it is the query that
*establishes* the tenant. A policy scoped by app.tenant_id would return zero rows
and break authentication outright. The table that establishes identity is the one
table the identity mechanism cannot protect.

Same shape as the public redirect, and the same answer. resolve_link() exists
because GET /r/<code> must read one row from links with no tenant to bind, and it
is a SECURITY DEFINER function returning exactly three columns. verify_api_key()
is that pattern applied to authentication: the app role loses SELECT on the table
and gains the ability to ask exactly one question.

What the function deliberately does NOT do: compare the secret. Hashing and the
constant-time comparison stay in app/auth.py, because a SQL comparison would be
short-circuiting and would leak timing. The function returns the hash for one
key_id and nothing else -- so a caller who already knows a key_id learns only that
key's hash, never anyone else's, and never the set of valid key_ids.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION verify_api_key(p_key_id text)
        RETURNS TABLE (tenant_id uuid, secret_hash text, revoked_at timestamptz)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT k.tenant_id, k.secret_hash, k.revoked_at
            FROM public.api_keys k
            WHERE k.key_id = p_key_id;
        $$;
        """
    )
    # Touching last_used_at also needs the elevation, since the app role is about
    # to lose UPDATE on the table.
    op.execute(
        """
        CREATE FUNCTION touch_api_key(p_key_id text)
        RETURNS void
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            UPDATE public.api_keys SET last_used_at = clock_timestamp()
            WHERE key_id = p_key_id;
        $$;
        """
    )

    # Default EXECUTE on a new function is granted to PUBLIC. Revoke first, then
    # grant deliberately -- otherwise this narrow capability is handed to every
    # role in the cluster, which is the whole thing being prevented.
    for fn in ("verify_api_key(text)", "touch_api_key(text)"):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC;")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO upsk_app;")

    # The app role can no longer read or write the credential table directly.
    op.execute("REVOKE SELECT, UPDATE ON api_keys FROM upsk_app;")


def downgrade() -> None:
    op.execute("GRANT SELECT, UPDATE ON api_keys TO upsk_app;")
    op.execute("DROP FUNCTION IF EXISTS touch_api_key(text);")
    op.execute("DROP FUNCTION IF EXISTS verify_api_key(text);")
