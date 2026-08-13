"""The live-schema assertion for the one piece of schema that is a security control.

Alembic answers "which revisions ran". It cannot answer "does the schema still
match them". A hand-run `ALTER POLICY` leaves the migration file unmodified and
`alembic_version` accurate, so the repo stays clean and `alembic upgrade head`
stays green while the policy is gone. That is not a gap alembic can close --
it records intent, and something has to record fact. This is that something.

A migration proves the schema was *once* correct. For schema that is a security
control, the interesting question is whether it is *still* correct.

Three things are asserted, and each covers a failure the other two cannot see:

  RLS enabled + FORCED   without FORCE, the owning role bypasses its own
                         policies silently -- policies that exist and do nothing

  USING *and* WITH CHECK losing only WITH CHECK is the quiet shape. Reads stay
                         sealed, so every read-side test keeps passing while
                         tenant B's writes land in tenant A's account, visible
                         to neither B nor the test suite. A read leak gets
                         noticed; this one surfaces in someone else's dashboard.

  partition exposure     policies on a partitioned parent are not inherited by
                         its partitions. Querying a partition directly applies
                         that partition's own policies, and it has none. Right
                         now that is closed by privilege rather than by RLS --
                         the app role simply has no grant on the partitions --
                         which means a later `GRANT ... ON ALL TABLES IN SCHEMA
                         public` would reopen it with no policy change to
                         review. Asserting privilege scope is what keeps the
                         guarantee from resting on a grant nobody remembers.

Comparison is on the *whole* predicate, not a fragment of it. Substring-matching
the current_setting(...) call would accept

    WITH CHECK (owner_id = (NULLIF(current_setting('app.tenant_id', true), ''))::uuid)

-- right function, wrong column, isolation gone. Equality on the normalised
expression is what makes the assertion mean what it claims to mean.

One implementation, three call sites: the CLI in scripts/check_rls.py, the boot
guard in app.db, and a CI step after `alembic upgrade`. A control with two
copies is a control that drifts.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

TABLES = ("links", "click_events")
POLICY_NAME = "tenant_isolation"

# The predicate both clauses must carry, written as Postgres renders it back
# through pg_policies -- not as the migration wrote it. The server re-prints
# expressions from the parse tree (casts made explicit, NULLIF upper-cased,
# parentheses normalised), so an expected value copied from the migration source
# would fail against a perfectly correct database.
EXPECTED_PREDICATE = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)

_POLICY_SQL = text(
    """
    SELECT c.relname            AS table_name,
           c.relrowsecurity     AS enabled,
           c.relforcerowsecurity AS forced,
           p.qual,
           p.with_check
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
    LEFT JOIN pg_policies p
           ON p.schemaname = n.nspname
          AND p.tablename = c.relname
          AND p.policyname = :policy
    WHERE c.relname = ANY(:tables)
    """
)

# Partitions of the tables above, with whether the app role can reach them
# directly and whether they carry RLS of their own. has_table_privilege() is
# asked rather than reading pg_class.relacl, because privilege can arrive
# through role membership or PUBLIC and relacl shows only the direct grant.
_PARTITION_SQL = text(
    """
    SELECT child.relname  AS partition_name,
           parent.relname AS parent_name,
           child.relrowsecurity AS enabled,
           has_table_privilege(:app_role, child.oid, 'SELECT') AS can_select,
           has_table_privilege(:app_role, child.oid, 'INSERT') AS can_insert
    FROM pg_inherits i
    JOIN pg_class parent ON parent.oid = i.inhparent
    JOIN pg_class child  ON child.oid  = i.inhrelid
    WHERE parent.relname = ANY(:tables)
    """
)


def connecting_role(dsn) -> str:
    """The role a DSN connects as.

    PostgresDsn is a *multi-host* URL in pydantic v2 -- Postgres accepts a host
    list for failover -- so the credential hangs off hosts()[0] and the URL
    itself has no .username. Derived from the DSN rather than added as a
    settings key on purpose: the role that gets checked is then, by
    construction, the role that connects. A separate key could disagree with
    the connection string, and a privilege check against the wrong role is
    worse than no check, because it reports green.
    """
    return dsn.hosts()[0]["username"]


def normalise(expr: str | None) -> str:
    """Whitespace- and case-insensitive form, for comparing rendered SQL.

    Postgres is free to re-print an expression with different spacing than it
    received. Comparing raw strings would make the control fire on a database
    that is entirely correct, and a control that cries wolf gets disabled.
    """
    return "".join(expr.split()).lower() if expr else ""


def find_drift(conn: Connection, app_role: str) -> list[str]:
    """Return one line per divergence; an empty list means the live schema matches.

    Pure query and comparison -- no printing, no exit. The CLI, the boot guard,
    and a test all share the judgment and differ only in what they do with the
    answer, which is the property that keeps the three from disagreeing.
    """
    expected = normalise(EXPECTED_PREDICATE)
    problems: list[str] = []

    rows = conn.execute(_POLICY_SQL, {"tables": list(TABLES), "policy": POLICY_NAME}).all()

    for missing in sorted(set(TABLES) - {r.table_name for r in rows}):
        problems.append(f"{missing}: table not found")

    for r in sorted(rows, key=lambda row: row.table_name):
        if not r.enabled:
            problems.append(f"{r.table_name}: RLS not enabled")
        if not r.forced:
            problems.append(
                f"{r.table_name}: RLS not FORCED -- the owner bypasses its own policies"
            )
        if r.qual is None and r.with_check is None:
            problems.append(f"{r.table_name}: no {POLICY_NAME} policy")
            continue
        for label, expr in (("USING", r.qual), ("WITH CHECK", r.with_check)):
            if normalise(expr) != expected:
                problems.append(
                    f"{r.table_name}: {label} is not the tenant predicate -> {expr!r}"
                )

    partitions = conn.execute(
        _PARTITION_SQL, {"tables": list(TABLES), "app_role": app_role}
    ).all()
    for p in partitions:
        if p.enabled:
            # The partition carries its own RLS, so a direct read is policed on
            # its own terms. Nothing to say.
            continue
        grants = (("SELECT", p.can_select), ("INSERT", p.can_insert))
        reachable = [verb for verb, granted in grants if granted]
        if reachable:
            problems.append(
                f"{p.partition_name}: reachable by {app_role} ({'/'.join(reachable)}) with no RLS "
                f"of its own -- {p.parent_name}'s policy does not apply to a partition queried "
                f"directly, so this bypasses tenant isolation"
            )

    return problems
