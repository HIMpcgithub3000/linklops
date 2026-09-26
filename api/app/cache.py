"""Cache-aside for the redirect hot path, plus the key discipline that stops a
cache from quietly becoming a hole in row-level security.

Two things live here and they are not the same thing.

**The cache.** Module 06 DECIDE chose cache-aside over write-through. The
deciding factor was not the stale window -- explicit invalidation closes that
either way -- but where Redis sits relative to the write path. Write-through
puts it *in* that path, so a Redis outage forces a choice between failing writes
and letting cache and database diverge in silence. Cache-aside keeps Redis
strictly optional, which is the property app/main.py `ready()` already committed
to when it deliberately left Redis out of readiness: a cache with a database
fallback must be allowed to degrade latency and never availability. Every Redis
call in this module therefore fails soft. A cache that can 500 the redirect is
worse than no cache, because it converts an optional dependency into a required
one without anyone deciding to.

**The key discipline.** The Module 06 constraints twist for b2b_multi_tenant is
org scoping, end to end, implemented now rather than deferred. The risk is
narrower and sharper than "put tenant_id in the keys":

    Row-level security is enforced by Postgres. A cache hit does not reach
    Postgres. So every cached read is a read with RLS switched off, and the
    key is the only thing standing between one tenant's data and another
    tenant's request.

That is not a hypothetical for a later module -- it is a property of the layer
being added right now. It is also silent when it breaks: a cross-tenant hit
returns a well-formed response, check_rls.py cannot see it because no query ran,
and nothing in the logs looks wrong. Hence `key()` below, which refuses to build
a key in a tenant-scoped namespace without a tenant, and `NAMESPACES`, which
makes "is this tenant-scoped?" a decision recorded once at registration instead
of a judgement re-made at each call site.

The redirect namespace is registered as NOT tenant-scoped, and that is
deliberate rather than an oversight: /r/{code} is public, `code` is globally
unique (uq_links_code), and tenant_id is an *output* of resolution -- see the
module docstring in app/routers/redirect.py. Writing the exception down next to
the rule is the point; an unexplained exception is indistinguishable from a bug.
"""

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass

from redis.exceptions import RedisError

from app import redis_client

log = logging.getLogger(__name__)


class CacheKeyError(RuntimeError):
    """A tenant-scoped namespace was asked for a key with no tenant.

    Deliberately not a soft failure. Everything else in this module degrades
    quietly because the database can answer instead; this one cannot, because
    there is no safe key to fall back to and the failure it prevents is a
    cross-tenant read. A missing cache is a slower service. A missing tenant in
    a key is a data leak, so it must be impossible to reach production, which
    means raising at the point of construction rather than logging and guessing.
    """


@dataclass(frozen=True)
class Namespace:
    name: str
    tenant_scoped: bool
    why: str


# Registered once. Adding a cache means adding a line here, which is the moment
# the tenant-scoping question gets asked -- rather than at a call site, where it
# gets asked only if someone remembers to.
NAMESPACES: dict[str, Namespace] = {
    ns.name: ns
    for ns in (
        Namespace(
            "redirect",
            tenant_scoped=False,
            why=(
                "public data plane; code is globally unique per uq_links_code and "
                "tenant_id is an output of resolution, never an input"
            ),
        ),
        # Nothing populates these yet. They are registered now because the
        # enforcement is only worth anything if it exists *before* the first
        # tenant-scoped cache does -- retrofitting a key shape means invalidating
        # every warm entry, and doing it under incident pressure means doing it
        # badly.
        Namespace(
            "links_page",
            tenant_scoped=True,
            why="management plane; a page of links belongs to exactly one tenant",
        ),
        Namespace(
            "click_totals",
            tenant_scoped=True,
            why="analytics rollups are per tenant and per link",
        ),
    )
}

# Short by design. TTL is the only bound on staleness that survives a missed
# invalidation, and one thing this cache cannot invalidate on is *time*:
# resolve_link() evaluates expiry at read time and no job fires when a link
# expires, so nothing exists to hook an invalidation onto. Sixty seconds is the
# resulting worst case -- an expired link may still redirect for up to a minute.
#
# Every other transition (destination changed, disabled, deleted) invalidates
# explicitly and is immediate. That asymmetry is the honest statement of what
# this cache costs: it slightly blunts a time-based expiry and does not blunt the
# kill switch at all, which is the trade worth making in that direction and not
# the other.
REDIRECT_TTL_SECONDS = 60

# Expiring a large set of keys minted in the same burst at the same instant is
# how a cache turns a traffic spike into a database spike. Jitter spreads that.
TTL_JITTER_SECONDS = 10

# How long a request that lost the fill race will wait for the winner before
# giving up and reading the database itself. Bounded hard: a redirect must never
# wait on a cache. 50ms is longer than a local fill and far shorter than any
# timeout a user would notice.
FILL_WAIT_SECONDS = 0.05
FILL_POLL_SECONDS = 0.005
FILL_LOCK_TTL_SECONDS = 5


def key(namespace: str, *parts: str, tenant_id: uuid.UUID | str | None = None) -> str:
    """Build a cache key, refusing to build an unscoped one where scope is required."""
    try:
        ns = NAMESPACES[namespace]
    except KeyError as exc:
        # An unregistered namespace has never had the tenant question asked of
        # it, so it is not safe to guess an answer.
        raise CacheKeyError(f"unregistered cache namespace {namespace!r}") from exc

    if ns.tenant_scoped and tenant_id is None:
        raise CacheKeyError(
            f"namespace {namespace!r} is tenant-scoped ({ns.why}); "
            "a key without a tenant would be readable across tenants"
        )
    if not ns.tenant_scoped and tenant_id is not None:
        # Also refused. A tenant appearing in a key the system believes is
        # global means one of the two is wrong, and silently honouring the
        # argument would fragment the cache while hiding the disagreement.
        raise CacheKeyError(
            f"namespace {namespace!r} is not tenant-scoped ({ns.why}); "
            "passing a tenant would silently fragment it"
        )

    scope = f"t:{tenant_id}:" if ns.tenant_scoped else ""
    return f"{namespace}:{scope}" + ":".join(parts)


def _ttl() -> int:
    return REDIRECT_TTL_SECONDS + random.randint(0, TTL_JITTER_SECONDS)


def get_redirect_target(code: str) -> tuple[uuid.UUID, str] | None:
    """Cached (link_id, long_url), or None for a miss OR any Redis failure.

    A miss and an outage are deliberately the same return value. The caller's
    correct behaviour is identical in both cases -- read the database -- and
    giving it two ways to be told so would invite a branch that treats one of
    them as fatal.
    """
    try:
        raw = redis_client.client.get(key("redirect", code))
    except RedisError as exc:
        log.warning("cache read failed, serving from database", extra={
            "namespace": "redirect", "error": type(exc).__name__,
        })
        return None

    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        return uuid.UUID(payload["id"]), payload["url"]
    except (ValueError, KeyError, TypeError):
        # A key whose shape we no longer understand -- an old format left by a
        # previous deploy, or a truncated value. Treated as a miss rather than
        # trusted, and dropped so it stops costing a parse on every request.
        log.warning("cache entry unreadable, discarded", extra={"namespace": "redirect"})
        invalidate_redirect_target(code)
        return None


def set_redirect_target(code: str, link_id: uuid.UUID, long_url: str) -> None:
    try:
        redis_client.client.set(
            key("redirect", code),
            json.dumps({"id": str(link_id), "url": long_url}),
            ex=_ttl(),
        )
    except RedisError as exc:
        log.warning("cache write failed", extra={
            "namespace": "redirect", "error": type(exc).__name__,
        })


def invalidate_redirect_target(code: str) -> None:
    """Drop a code's entry. Called on every mutation of a resolution input.

    Failure here is logged at ERROR, not WARNING, and that difference is the
    point. A failed read or write costs latency. A failed *invalidation* means
    the cache is now knowingly serving something the database no longer says --
    including, after a disable, a link someone tried to switch off. It is
    bounded by the TTL rather than unbounded, which is the only reason this is
    survivable at all, and it is the one cache failure a person should look at.
    """
    try:
        redis_client.client.delete(key("redirect", code))
    except RedisError as exc:
        log.error("cache invalidation failed, stale until TTL", extra={
            "namespace": "redirect",
            "ttl_bound_seconds": REDIRECT_TTL_SECONDS + TTL_JITTER_SECONDS,
            "error": type(exc).__name__,
        })


def fill_once(code: str, load):
    """Single-flight around a miss, so one expiring hot key does not become N
    identical database reads.

    This is the failure the module's incident describes: a cache makes a system
    faster in tests and then, on the first spike after a hot key expires, hands
    the database every request that used to be absorbed. The fix is not a longer
    TTL -- that just moves the cliff -- it is making sure one miss produces one
    load.

    Losers wait briefly for the winner's fill and then read the database
    themselves rather than waiting longer. The bound matters more than the
    saving: a redirect that blocks on a lock has traded an availability property
    for a load property, which is the wrong direction for the one endpoint that
    is the product.
    """
    lock = f"lock:{key('redirect', code)}"
    try:
        won = redis_client.client.set(lock, "1", nx=True, ex=FILL_LOCK_TTL_SECONDS)
    except RedisError:
        # No lock available means no coordination, not no service.
        won = True

    if won:
        try:
            return load()
        finally:
            try:
                redis_client.client.delete(lock)
            except RedisError:
                pass  # It expires on its own; a stuck lock costs one TTL of herd.

    # The loser waits, briefly, and this is the line the whole protection lives
    # on. Removing it looks like a latency win -- polling costs up to
    # FILL_WAIT_SECONDS, the database read is one indexed function call, and the
    # winner still populates the cache for whoever comes next. Every clause of
    # that is true and the conclusion is still wrong: if losers read through,
    # then N concurrent misses on one key produce N database reads, which is
    # precisely the stampede this function exists to stop. Measured at 20
    # concurrent requests on a cold key: 1 database read with the wait, 20
    # without, and the responses are identical either way.
    #
    # The bound is what makes the wait safe. It is capped, it ends in a database
    # read rather than an error, and it never exceeds FILL_WAIT_SECONDS -- so the
    # worst case is a slightly slower redirect, not a redirect that hangs on a
    # lock.
    deadline = time.monotonic() + FILL_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(FILL_POLL_SECONDS)
        filled = get_redirect_target(code)
        if filled is not None:
            return filled
    return load()
