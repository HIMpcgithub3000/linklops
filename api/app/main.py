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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app import db, logging_config
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
        log.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
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
    return JSONResponse(
        status_code=400,
        content={"detail": "request rejected", "problems": problems},
    )


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
        return JSONResponse(
            status_code=403,
            content={"detail": "not permitted for this tenant"},
        )
    # ERROR with the traceback, because this is the branch where the response
    # body is deliberately uninformative. The public answer is "internal error";
    # the log is the only place the actual cause survives, so if it is not
    # written here it is not written anywhere.
    log.error("unhandled database error", exc_info=exc, extra={"sqlstate": sqlstate})
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness. Deliberately dependency-free -- see module docstring."""
    return {"ok": True}


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
