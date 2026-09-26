"""API entrypoint.

Importing app.config runs configuration validation, so bad config kills the
process here -- before FastAPI is constructed and long before uvicorn binds.

/health and /ready answer different questions and must never be merged:

  /health   liveness    "is this process wedged?"   -> the only useful action
            on failure is restart, so it may only test things a restart can
            fix. Checking the database here would mean a 30-second DB blip
            restarts every pod at once, converting a partial outage into a
            total one.

  /ready    readiness   "should I receive traffic?" -> checks hard
            dependencies. Failure pulls this instance out of rotation without
            killing it, so it rejoins by itself when the dependency returns.

Both are unauthenticated by necessity -- a load balancer cannot hold a
credential -- so neither body carries version, hostname, dependency names, or
error text. In a B2B product an unauthenticated endpoint enumerating the
infrastructure is free reconnaissance.
"""

import logging
import signal
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db, logging_config, metrics
from app.config import settings
from app.routers import links, redirect

# Postgres reports a row-level security WITH CHECK violation as
# insufficient_privilege. It is an authorization outcome, not a fault, so it
# must not be reported as one.
SQLSTATE_INSUFFICIENT_PRIVILEGE = "42501"

# Flipped by SIGTERM so /ready fails before the server stops accepting.
_shutting_down = False
_previous_sigterm = None


def _begin_shutdown(signum, frame) -> None:
    """Flip readiness false, then hand off to uvicorn's own handler.

    Ordering is the entire point. On SIGTERM the process starts refusing
    connections almost immediately, but the load balancer only notices at its
    next probe -- so without this, every request routed in that window fails.
    A self-inflicted outage on every deploy, caused by the readiness endpoint
    that exists to prevent outages.

    Chained rather than replacing: uvicorn installs its own SIGTERM handler
    for graceful shutdown, and overwriting it would trade request loss at the
    start of shutdown for a hung process at the end.
    """
    global _shutting_down
    _shutting_down = True
    if callable(_previous_sigterm):
        _previous_sigterm(signum, frame)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Installed here, not at import. uvicorn calls install_signal_handlers()
    # inside uvicorn.run(), which is *after* this module is imported -- so a
    # handler registered at import time gets silently overwritten and the
    # chaining above would capture Python's default instead of uvicorn's.
    # Lifespan startup runs after uvicorn has installed its own, so this is
    # the first point at which "previous" means what it claims to.
    global _previous_sigterm
    _previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _begin_shutdown)

    # One startup line naming what this process believes it is. Added because a
    # checklist asked whether the logs show the environment and the honest answer
    # was no -- every line until now was request-scoped, so a container that
    # never received a request said nothing at all about itself.
    #
    # The field list is an allowlist, and the omissions are the point: no
    # DATABASE_URL, no REDIS_URL, no secret of any kind. Same reasoning as
    # ECHO_SAFE in app/config.py -- default-deny, because the cost of the two
    # mistakes is wildly asymmetric. What is here is what an on-call engineer
    # needs to answer "is this container the one I think it is": which
    # environment it was configured as, which version, and where it is listening.
    log.info(
        "service starting",
        extra={
            "environment": settings.ENVIRONMENT,
            "version": app.version,
            # The CONFIGURED port, which is not necessarily the bound one: the
            # container CMD passes ${PORT} to uvicorn so they agree there, but a
            # local run with --port overrides it and this field would then be
            # confidently wrong. Named for what it actually knows.
            "configured_port": settings.PORT,
            "log_level": settings.LOG_LEVEL,
        },
    )
    yield


app = FastAPI(title="Link Shortener API", version="0.4.0", lifespan=lifespan)

# The level comes from configuration and from nowhere else.
#
# What was here before: LOGGING_LEVEL_OVERRIDE = "warning", then
# configure(LOGGING_LEVEL_OVERRIDE or settings.LOG_LEVEL). The `or` never
# reached the setting, because a non-empty string is truthy -- so .env could say
# LOG_LEVEL=info, every layer could report success, and the service still ran at
# warning. The failure was silent in both directions: nothing warned that the
# setting was ignored, and the only evidence was an absence of log lines, which
# is the hardest kind of evidence to notice.
#
# This is the same shape as the APP_PORT incident that motivated the config
# contract in the first place -- a value that is set, read, and quietly
# discarded. The contract checks in app/config.py cannot catch this one: the key
# exists, parses, and is valid. It is simply never used. The general rule the
# two incidents share: a setting with a second source of truth has no source of
# truth, and `a or b` is how a second one gets added without anyone deciding to.
logging_config.configure(settings.LOG_LEVEL)
log = logging.getLogger("api")


def envelope(status_code: int, code: str, message: str, **extra) -> JSONResponse:
    """One response shape for every error, 4xx and 5xx alike.

    Three fields, and the third is the one that changed my mind. `code` is a
    stable string clients may branch on -- stable meaning it outlives message
    rewording, so a client that keys on it does not break when we improve the
    prose. `message` is for a human and is deliberately never derived from the
    exception. `request_id` is internal state, and it is the ONE piece worth
    exposing: it is random, it means nothing without our logs, and it converts an
    unactionable "it broke" into a searchable request.

    Before this, the request id rode only in a header, which meant a person
    looking at a failure in a UI never saw it -- so "quote me the id" assumed an
    API consumer rather than an end user.

    The codes stay deliberately coarse where distinguishing them would disclose.
    Every unresolvable redirect is `not_found`; every one of the five auth
    failure modes is `unauthenticated`. A finer code would re-create the oracle
    the coarse HTTP status was chosen to avoid.
    """
    body = {"error": {"code": code, "message": message,
                      "request_id": logging_config.request_id.get() or "-"}}
    if extra:
        body["error"].update(extra)
    return JSONResponse(status_code=status_code, content=body)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Route HTTPException through the same envelope.

    Without this, handler-raised 401s and 404s keep FastAPI's {"detail": ...}
    shape while everything else uses the envelope -- two formats again, which is
    the exact inconsistency an earlier module cost me a whole finding to
    discover.
    """
    codes = {400: "bad_request", 401: "unauthenticated", 403: "forbidden",
             404: "not_found", 429: "rate_limited"}
    code = codes.get(exc.status_code, "error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    response = envelope(exc.status_code, code, message)
    for k, v in (exc.headers or {}).items():
        response.headers[k] = v
    return response


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    """Last resort. Logs with the traceback, returns nothing about it."""
    log.error("unhandled exception", exc_info=exc)
    return envelope(500, "internal_error", "Something went wrong on our side.")


@app.middleware("http")
async def request_logging(request: Request, call_next):
    """One request received line in, one request completed line out.

    The completion line is guaranteed and identically shaped for every endpoint
    and every outcome, including exceptions -- so "how long did requests take"
    and "what fraction returned 5xx" are one query, not one query per route.
    Emitting it from a finally block is the whole point: a completion line that
    only appears on the happy path is missing for exactly the requests worth
    investigating.

    req_id is taken from the caller's X-Request-Id when present so a trace
    survives across services, and minted here when absent.
    """
    rid = request.headers.get("X-Request-Id") or logging_config.new_request_id()
    logging_config.request_id.set(rid)
    logging_config.tenant_id.set(request.headers.get("X-Dev-Tenant-Id"))

    log.info("request received", extra={"method": request.method, "path": request.url.path})
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = rid
        return response
    finally:
        elapsed = time.perf_counter() - started
        # Route TEMPLATE, not the raw path, so /r/aB3xY9k and /r/Zq1mn8P share
        # one metric series instead of minting one per short code (unbounded
        # cardinality). Falls back to "unmatched" for 404s on no route, which is
        # itself one bounded series rather than one per hostile URL probed.
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"
        metrics.observe(request.method, template, status_code, elapsed)
        log.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "duration_ms": round(elapsed * 1000, 1),
            },
        )


# CORS derived from ENVIRONMENT, not from a separate key. A permissive origin
# list is a development convenience and a production vulnerability, so the value
# that decides it is the one value that is already required, validated against a
# Literal, and has no default -- there is no CORS_ORIGINS to forget to set,
# because forgetting it would mean falling back to the permissive branch.
#
# The list is empty rather than "*" outside development: this API is consumed by
# the tenant dashboard on PUBLIC_BASE_URL and by nothing else, and a wildcard
# would let any site a customer visits issue authenticated requests against it
# once Module 4 lands cookies.
_dashboard_origin = str(settings.PUBLIC_BASE_URL).rstrip("/")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENVIRONMENT == "development" else [_dashboard_origin],
    allow_credentials=settings.ENVIRONMENT != "development",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Dev-Tenant-Id", "X-Request-Id"],
)

app.include_router(links.router)
app.include_router(redirect.router)


@app.exception_handler(RequestValidationError)
async def rejected_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Answer 400 for a request this API refused, and do not echo the input.

    Two deliberate departures from the framework default.

    Status: FastAPI answers 422. Defensible, but this API answers 400, because
    from the caller's side "well-formed JSON, unacceptable value" and "malformed
    request" have one remediation -- fix the request -- and 422 is a WebDAV code
    that intermediaries and generated clients handle inconsistently. One code
    for one condition.

    Body: the default handler includes an `input` field carrying the offending
    value verbatim. That value is attacker-controlled by definition, and this
    endpoint's whole job is rejecting hostile URLs -- javascript: payloads, bidi
    overrides, control characters -- so reflecting it lands exactly those bytes
    in the response, the access log, and whatever console reads it. Same
    reasoning as ECHO_SAFE in app/config.py: the field name and the reason are
    the diagnostic that matters, and neither is derived from the input.
    """
    problems = [
        {"field": ".".join(str(part) for part in err["loc"]), "reason": err["msg"]}
        for err in exc.errors()
    ]
    return envelope(400, "validation_failed", "One or more fields were rejected.",
                    problems=problems)


@app.exception_handler(DBAPIError)
async def database_error(_: Request, exc: DBAPIError) -> JSONResponse:
    """Map an RLS denial to 403; report everything else as an opaque 500.

    Without this, a WITH CHECK violation -- a write aimed at another tenant --
    surfaces as a 500, which says "we are broken" about the isolation mechanism
    working exactly as designed. It would also be indistinguishable from a real
    fault in dashboards and alerting, so the one event most worth noticing gets
    filed with the noise.

    Everything else returns a bodyless-detail 500 on purpose: driver exceptions
    carry SQL fragments, column names and sometimes parameter values, and this
    response is public.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == SQLSTATE_INSUFFICIENT_PRIVILEGE:
        # WARNING, not ERROR. A cross-tenant write being refused is the isolation
        # mechanism working; it needs to be visible and counted, but nobody is
        # paged for a policy doing its job. Logged with the sqlstate so it can be
        # aggregated -- a rising rate here is an application bug or an attack,
        # and either way the signal is the rate, not the individual line.
        log.warning("write refused by tenant policy", extra={"sqlstate": sqlstate})
        return envelope(403, "forbidden", "Not permitted for this tenant.")
    # ERROR, because this is the branch where the response body is deliberately
    # uninformative: the log is the only place the cause survives, so if it is
    # not written here it is not written anywhere.
    #
    # But NOT exc_info. A psycopg error renders as its full server message, and
    # for a constraint violation that message carries a DETAIL line listing the
    # entire failing row -- every column value, which on `invitations` means the
    # invitee's email address, and on any future table means whatever that table
    # holds. That is a copy of production data in the log stream, written by the
    # error path rather than by anything anyone reviewed, and this stream is
    # exported to customers.
    #
    # Nothing diagnostic is given up for that. Postgres already separates the
    # two: `diag` exposes the failure as fields, and only `message_detail`
    # carries row values -- so the constraint, the table and the primary message
    # are logged and the values are not. The Python frames are kept separately
    # via format_tb, which renders where the statement was issued without
    # rendering the exception's text; that is the half of a traceback with
    # diagnostic value here, and the half that cannot carry a row.
    diag = getattr(exc.orig, "diag", None)
    log.error(
        "unhandled database error",
        extra={
            "sqlstate": sqlstate,
            "exc_type": type(exc.orig).__name__,
            "pg_message": getattr(diag, "message_primary", None),
            "pg_constraint": getattr(diag, "constraint_name", None),
            "pg_table": getattr(diag, "table_name", None),
            "pg_column": getattr(diag, "column_name", None),
            # Last frames only: the deep half is SQLAlchemy's own stack, which
            # is identical for every error and says nothing about this one.
            "frames": [f.rstrip() for f in traceback.format_tb(exc.__traceback__)[-4:]],
        },
    )
    return envelope(500, "internal_error", "Something went wrong on our side.")


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness. Deliberately dependency-free -- see module docstring."""
    return {"ok": True}


@app.get("/metrics")
def metrics_endpoint() -> Response:
    """Prometheus scrape target (Module 04). Pull-based: this returns the current
    values and knows nothing about who reads them.

    Unauthenticated like /health and /ready, and for the same reason a scraper
    cannot hold a credential -- but that means the label set is a disclosure
    surface, which is exactly why `path` is the route template, not the raw URL:
    the metric names routes this service has, never the specific ids or codes a
    request carried. No metric here reveals a tenant, a code, or a payload.
    """
    payload, content_type = metrics.render()
    return Response(content=payload, media_type=content_type)


@app.get("/ready")
def ready(response: Response) -> dict[str, Any]:
    """Readiness. Postgres only.

    Redis arrives in Module 6 and must NOT be added here: it is a cache with a
    database fallback, so a Redis outage would drop the whole fleet out of
    rotation for a service that could still serve, just slower. A more
    thorough check that makes availability worse is the failure mode to avoid.
    """
    if _shutting_down:
        response.status_code = 503
        return {"ok": False}
    try:
        db.ping()
    except Exception:
        # Swallowed on purpose. The load balancer needs a status code, not a
        # stack trace, and this response is public.
        response.status_code = 503
        return {"ok": False}
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    # Bound to loopback, not 0.0.0.0: local dev has no reason to be reachable
    # from the LAN. HOST becomes an env var when this is containerised, since
    # a container must bind 0.0.0.0 to be reachable through its port mapping.
    uvicorn.run(app, host="127.0.0.1", port=settings.PORT)
