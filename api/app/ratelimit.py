"""Rate limits, keyed by what the limit is actually protecting.

The matrix and the reasoning behind each row:

| surface        | key       | limit    | protects against                        |
|----------------|-----------|----------|-----------------------------------------|
| auth failures  | key_id    | 10/min   | credential brute force                  |
| create link    | tenant    | 60/min   | abuse volume -> shared-domain blocklist |
| redirect       | client IP | 600/min  | short-code enumeration                  |
| list/analytics | tenant    | 120/min  | bulk scraping of a tenant's own data    |

Three deliberate choices.

**Different keys per surface, because the attacker is different.** Create is
keyed by tenant, since the threat is one account producing abusive volume and an
IP limit would punish a legitimate customer behind a NAT. Redirect is keyed by IP,
since there is no account at all -- the caller is anonymous by design. Auth
failures are keyed by key_id, because keying by IP lets a distributed attacker
walk one key, and keying by tenant is impossible before the key is verified.

**The redirect limit is high and still worth having.** 600/min will not stop a
determined enumerator, and it is not meant to -- code entropy does that. It caps
the rate at which someone can search the space and, more usefully, makes the
attempt *visible* as a rate rather than as a diffuse background of 404s.

**Fail open, not closed.** If Redis is unavailable the request proceeds. A rate
limiter that takes the service down when its own dependency fails has converted
an abuse control into an outage, and this service already treats Redis as
optional on the redirect path for the same reason.
"""

import logging

from fastapi import HTTPException, Request, status

from app import redis_client

log = logging.getLogger(__name__)

LIMITS = {
    "auth_fail": (10, 60),
    "create": (60, 60),
    "redirect": (600, 60),
    "read": (120, 60),
}


def check(surface: str, identity: str) -> None:
    """Count one event against a fixed window; raise 429 past the limit.

    A fixed window, not a sliding one: it is one INCR and one EXPIRE, and its
    known flaw -- up to 2x the limit across a window boundary -- does not matter
    for any of the surfaces above, where the limits are order-of-magnitude
    judgements rather than precise budgets.
    """
    limit, window = LIMITS[surface]
    bucket = f"rl:{surface}:{identity}"
    try:
        count = redis_client.client.incr(bucket)
        if count == 1:
            redis_client.client.expire(bucket, window)
    except Exception as exc:  # noqa: BLE001
        log.warning("rate limit unavailable, allowing request: %s", type(exc).__name__)
        return

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            # Retry-After is the one piece of detail worth returning: without it
            # a well-behaved client retries immediately and makes things worse.
            headers={"Retry-After": str(window)},
        )


def client_ip(request: Request) -> str:
    """The redirect path has no principal, so the limit is keyed by address.

    Deliberately reads the socket peer and NOT X-Forwarded-For. Behind a trusted
    proxy that header is correct; with no proxy it is client-supplied, and a
    rate-limit key an attacker can choose is not a rate limit. Reinstate it only
    once a proxy is known to be terminating traffic and stripping the inbound
    value.
    """
    return request.client.host if request.client else "unknown"
