#!/usr/bin/env python
"""The control I proposed in four modules and did not build until now.

check_rls.py inspects a hardcoded list of two tables. So every tenant-scoped
table added since has joined the schema unexamined -- three of them, in the
Team Collaboration work, found only because a review step happened to prompt a
look. This check removes the hardcoded list.

It enumerates EVERY table in the public schema, asks which of them carry a
`tenant_id` column, and fails on any that has neither a row-level-security
policy nor an explicit, documented exemption below. Absence of a policy is now
a build failure rather than a thing nobody looks at.

The exemption list is the interesting part. An exemption is a claim, so each one
carries the compensating control that makes it safe, and the list is short on
purpose: a growing exemption list is the check being defeated politely.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.rls import connecting_role  # noqa: E402

# table -> why it cannot carry a policy, and what protects it instead.
EXEMPTIONS = {
    "api_keys": (
        "Read BEFORE a tenant is bound -- it is the query that ESTABLISHES the "
        "tenant, so a policy scoped by app.tenant_id would return zero rows and "
        "break authentication. Compensating control: the app role has no SELECT "
        "on it at all; access is via verify_api_key(), a SECURITY DEFINER "
        "function returning exactly three columns for one key_id (migration 0008)."
    ),
}

# Two corrections, both found by running the first version of this check and
# reading its output instead of trusting it:
#
#   * REACHABILITY. v1 flagged three click_events partitions. The app role has
#     no grant on them at all -- only on the partitioned parent, which does
#     carry a policy -- so they are unreachable and the finding was noise.
#     A table the app role cannot query is not an exposure. Filter on the grant.
#
#   * TRANSITIVE SCOPING. v1 missed team_memberships and invitations entirely,
#     because it looked for a `tenant_id` COLUMN and those two are tenant-scoped
#     through a foreign key to `teams`. That was the whole bug it was built to
#     catch, reproduced inside the catcher one layer down.
#
# So: a table is in scope if the app role can read it AND it either carries
# tenant_id or references something that does.
SQL = text(
    """
    WITH readable AS (
        SELECT c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p') AND n.nspname = 'public'
          AND has_table_privilege(:app_role, c.oid, 'SELECT')
    ),
    direct AS (
        SELECT r.oid FROM readable r
        JOIN pg_attribute a ON a.attrelid = r.oid AND a.attname = 'tenant_id'
                            AND a.attnum > 0 AND NOT a.attisdropped
    ),
    transitive AS (
        SELECT con.conrelid AS oid
        FROM pg_constraint con
        WHERE con.contype = 'f' AND con.confrelid IN (SELECT oid FROM direct)
    )
    SELECT r.relname AS table_name,
           r.relrowsecurity AS rls_enabled,
           r.relforcerowsecurity AS rls_forced,
           (SELECT COUNT(*) FROM pg_policy p WHERE p.polrelid = r.oid) AS policies,
           (r.oid IN (SELECT oid FROM direct)) AS has_tenant_column
    FROM readable r
    WHERE r.oid IN (SELECT oid FROM direct) OR r.oid IN (SELECT oid FROM transitive)
    ORDER BY r.relname;
    """
)


def main() -> int:
    engine = create_engine(str(settings.MIGRATION_DATABASE_URL))
    with engine.connect() as conn:
        rows = conn.execute(SQL, {"app_role": connecting_role(settings.DATABASE_URL)}).all()

    if not rows:
        print("FAIL: no tenant-scoped tables reachable at all -- is this the right database?")
        return 1

    failures, exempt, ok = [], [], []
    for r in rows:
        if r.table_name in EXEMPTIONS:
            exempt.append(r.table_name)
        elif not r.rls_enabled or not r.rls_forced or r.policies == 0:
            failures.append(
                f"  {r.table_name}: enabled={r.rls_enabled} forced={r.rls_forced} "
                f"policies={r.policies} "
                f"({'tenant_id column' if r.has_tenant_column else 'FK to a tenant-scoped table'})"
            )
        else:
            ok.append(r.table_name)

    print(f"tenant-scoped tables: {len(rows)}")
    print(f"  protected: {', '.join(ok) or '(none)'}")
    print(f"  exempt:    {', '.join(exempt) or '(none)'}")
    if failures:
        print("FAIL -- tenant-scoped with no policy and no documented exemption:")
        print("\n".join(failures))
        print(
            "\nAdd a tenant_isolation policy with USING *and* WITH CHECK, plus "
            "FORCE ROW LEVEL SECURITY -- or add an entry to EXEMPTIONS naming the "
            "compensating control."
        )
        return 1

    print("OK -- every tenant-scoped table is protected or explicitly exempt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
