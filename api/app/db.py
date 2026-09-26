"""Engine, session factory, and the request-scoped tenant binding.

This module is where the RLS guarantee is actually delivered. The policy in
the migration is only half of it -- a policy with nothing setting
app.tenant_id matches zero rows, and a policy set on the wrong scope leaks.
"""

import sys
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.rls import connecting_role, find_drift

# How the request's tenant is established. Module 4 replaces this with a
# verified JWT claim; until then it is a request header, which is forgeable by
# definition. The guard below is what stops that from ever reaching a real
# deployment -- and it removes itself, because M4 changes this constant and
# the check stops firing.
AUTH_BACKEND = "api-key"
DEV_TENANT_HEADER = "X-Dev-Tenant-Id"

if AUTH_BACKEND == "dev-header" and settings.ENVIRONMENT != "development":
    # Fail at startup, not per request. A client-supplied tenant id that
    # survives into production is a trivially forged authorization bypass --
    # the single worst thing on this list to discover later. Refusing to boot
    # makes "we shipped before auth landed" a loud failure instead of a hole.
    print(
        f"FATAL: AUTH_BACKEND={AUTH_BACKEND!r} is development-only, but "
        f"ENVIRONMENT={settings.ENVIRONMENT!r} -- refusing to start.\n"
        "  This backend trusts a client-supplied header for tenant identity.\n"
        "  fix: land real authentication (Module 4) before running outside development",
        file=sys.stderr,
    )
    raise SystemExit(1)


engine = create_engine(
    str(settings.DATABASE_URL),
    pool_pre_ping=True,   # a connection killed by a DB restart fails the checkout, not the query
    pool_size=5,
    max_overflow=5,
    # Explicit timeouts, added for deployment (Module 10). The module's failure
    # mode is a hang, not a crash: a wedged database or an exhausted pool that
    # waits forever holds a worker thread, and enough of them stall the service
    # behind a dependency that is never coming back. Every wait here is bounded.
    #
    #   pool_timeout   how long a request waits for a free pooled connection
    #                  before giving up. Unbounded by default -- so under a
    #                  connection spike the symptom is requests that hang rather
    #                  than a clean 503 the load balancer can act on.
    #   connect_timeout  (libpq, via connect_args) how long the initial TCP+auth
    #                  handshake may take. Without it a network black hole makes
    #                  a new connection block for the OS default of ~2 minutes.
    #
    # statement_timeout is NOT set globally here: a background rollup and a
    # redirect have different right answers for "too long", so it is set per
    # transaction where it matters (see ping() below and the worker) rather than
    # as one number that is wrong for one of them.
    pool_timeout=5,
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _assert_environment_matches() -> None:
    """Refuse to run against a database that says it belongs elsewhere.

    Every other configuration check in this service validates the shape of a
    value. None of them can catch DATABASE_URL pointing at the wrong database,
    because a production connection string is exactly as well-formed as a staging
    one -- required, non-empty, parseable, valid. The check has to ask the
    database, because only the database knows which one it is.

    Fails closed on a mismatch and fails OPEN when the table is absent, which is
    a deliberate asymmetry: an older database that predates migration 0005 has
    not made a claim, and refusing to boot on "no claim" would make this control
    an outage during the rollout that introduces it. A wrong claim is evidence; a
    missing claim is silence, and those deserve different responses.
    """
    try:
        with engine.connect() as conn:
            claimed = conn.execute(
                text("SELECT environment FROM deployment_identity")
            ).scalar()
    except OperationalError:
        return  # unreachable database: handled by the readiness path, not here
    except ProgrammingError:
        print(
            "WARNING: this database has no deployment_identity row, so it cannot "
            "state which environment it belongs to. Run `alembic upgrade head`.",
            file=sys.stderr,
        )
        return

    if claimed != settings.ENVIRONMENT:
        print(
            f"FATAL: environment mismatch -- refusing to start.\n"
            f"  this process is configured as ENVIRONMENT={settings.ENVIRONMENT!r}\n"
            f"  the database it was pointed at says it is {claimed!r}\n"
            "  One of the two is wrong, and the expensive case is a lower environment\n"
            "  holding a production connection string, so neither is assumed correct.\n"
            "  fix: correct DATABASE_URL, or correct ENVIRONMENT -- not this check.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _assert_rls_intact() -> None:
    """Refuse to serve on a database whose tenant isolation has drifted.

    Boot, not /ready, and the distinction is not stylistic:

      cost      the assertion reads pg_class and pg_policies. On /ready that is
                a catalog round trip every probe tick, forever, for a condition
                that changes approximately never.

      semantics readiness means "temporarily cannot serve, come back". Policy
                drift is permanent until a human repairs it, so failing /ready
                would quietly pull every instance out of the load balancer and
                page on-call with unexplained capacity loss -- not with "tenant
                isolation is off". The alert that fires must name the thing that
                is wrong, or the outage is diagnosed twice.

    Two failures, deliberately not treated alike:

      unreachable   the database is down, or not up yet during a rolling deploy.
                    Transient, and /ready already holds traffic off an instance
                    that cannot reach Postgres. Killing the process here would
                    turn a database blip into a deploy that cannot roll, so this
                    warns and continues -- the instance boots and stays out of
                    rotation until the dependency returns.

      drift         the database answered and the answer was wrong. Permanent,
                    and every second of serving is cross-tenant writes. Refuse.

    Lives at import of app.db, which the API server and the scripts import and
    alembic/env.py does not. That asymmetry is load-bearing: `alembic upgrade
    head` is the repair path, so a guard that blocked it would make drift
    unfixable without first deleting the guard.
    """
    try:
        with engine.connect() as conn:
            problems = find_drift(conn, app_role=connecting_role(settings.DATABASE_URL))
    except OperationalError as exc:
        print(
            f"WARNING: could not verify tenant isolation at startup: "
            f"{type(exc.orig).__name__ if exc.orig else type(exc).__name__}.\n"
            "  Booting anyway -- /ready will hold traffic until Postgres answers.",
            file=sys.stderr,
        )
        return

    if problems:
        print(
            "FATAL: tenant isolation has drifted from the migrations -- refusing to start.",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "  Serving now would write rows across tenant boundaries.\n"
            "  fix: `alembic upgrade head` re-asserts the policies; a grant reported above\n"
            "       was never in a migration and has to be revoked at the source.\n"
            "  then: `python scripts/check_rls.py` to confirm before restarting",
            file=sys.stderr,
        )
        raise SystemExit(1)


_assert_environment_matches()
_assert_rls_intact()


def resolve_tenant_id(request: Request) -> str:
    """The tenant the request is bound to, taken from the authenticated principal.

    Set by the auth dependency before the session is opened. Reading it from
    request.state rather than from a header is the whole point of Module 04: the
    previous version trusted X-Dev-Tenant-Id, which a caller supplied and could
    therefore choose.
    """
    tenant_id = getattr(request.state, "principal_tenant_id", None)
    if not tenant_id:
        # Reached only if a route wires get_session without the auth dependency.
        # Failing closed here means the mistake is an error rather than a silent
        # unauthenticated read.
        raise ValueError("no authenticated principal on this request")
    return tenant_id


def bind_tenant(session: Session, tenant_id: str) -> None:
    """Bind this transaction to a tenant, for RLS to read.

    Two things here are load-bearing and easy to get wrong:

    1. set_config(..., is_local => true), not `SET`. `SET LOCAL` cannot take a
       bind parameter, so the obvious form is string interpolation -- SQL
       injection through the tenant isolation mechanism itself. set_config()
       is a function, so the value is a real parameter.

    2. is_local => true scopes the setting to the transaction. Setting it on
       the connection would leave it there when the pool hands that connection
       to the next request, so request N+1 would read request N's tenant --
       a cross-tenant read *caused by* the isolation mechanism.
    """
    session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


def get_session(request: Request) -> Iterator[Session]:
    """Tenant-scoped session for the management plane.

    Every query issued through this session is filtered by the RLS policy
    whether or not the caller remembers to write WHERE tenant_id = ?. That is
    the whole point: forgetting returns zero rows, not everyone's rows.
    """
    tenant_id = resolve_tenant_id(request)
    with SessionLocal() as session:
        with session.begin():
            bind_tenant(session, tenant_id)
            yield session


def get_public_session() -> Iterator[Session]:
    """Untenanted session for the public redirect path.

    Deliberately does NOT bind a tenant, because GET /r/<code> has none. Under
    the RLS policy this session can therefore read nothing from links at all --
    which is correct. It reaches the one row it is allowed to see through
    resolve_link(), the SECURITY DEFINER function that is the single audited
    tenant-free read in the system.
    """
    with SessionLocal() as session:
        with session.begin():
            yield session


def ping(timeout_seconds: int = 2) -> None:
    """Readiness probe for a hard dependency. Raises if unusable.

    The timeout is not optional. An unbounded probe against a hung database
    hangs the probe, times out the health check, and turns the readiness
    endpoint itself into the outage.
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = {timeout_seconds * 1000}"))
        conn.execute(text("SELECT 1"))
