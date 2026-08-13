"""The public data plane: GET /r/{code}.

The one route in this system with no tenant. It runs on get_public_session,
which never binds app.tenant_id and therefore -- under the RLS policy -- can
read nothing from links at all. It reaches the single row it is allowed to see
through resolve_link(), the SECURITY DEFINER function that is the only audited
tenant-free read in the system. tenant_id is an *output* of this path, never an
input.

Prefixed /r/ rather than served at the root. A root catch-all would make the
code namespace and the route namespace the same namespace, so every future path
becomes a reserved word that has to be carved out of the code space before
someone mints a colliding code -- and it would swallow routing mistakes, since a
typo'd admin route would fall through to here and 404 as a missing code rather
than as a missing route.
"""

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.orm import Session

from app import redis_client
from app.db import get_public_session
from app.services import links_service

router = APIRouter(tags=["redirect"])

logger = logging.getLogger(__name__)


def enqueue_click(link_id: uuid.UUID) -> None:
    """Hand the click to the analytics worker, and never fail the redirect for it.

    A Redis LPUSH, not a database INSERT. System Design Module 03 deliberately
    kept a write off this path; that reasoning was about the *database* write --
    coupling the analytics write path to the hot read path is what the 1000:1
    read/write ratio forbids. A fire-and-forget push to a queue keeps that
    separation: the expensive part (the rollup upsert) happens in another
    process, on its own schedule.

    Swallowing the exception is the deliberate part. Analytics is a rollup
    counter; a redirect is the product. If Redis is down, the correct behaviour
    is to serve the redirect and lose the count, not to 500 a working link
    because a counter is unavailable. Logged at warning so the loss is visible
    rather than silent -- an undercount nobody can see is worse than one
    everybody can.
    """
    try:
        redis_client.client.lpush(redis_client.ANALYTICS_QUEUE, str(link_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("analytics enqueue failed, click not counted: %s", type(exc).__name__)

# Shape-checked before it reaches the database. Not a security control -- the
# lookup is parameterised, so a hostile code is a failed match, not an injection
# -- but an unbounded path segment is a free database round trip per request on
# the highest-traffic endpoint in the product, and this is the cheapest place to
# refuse one. Bounds match the column: VARCHAR(32) with ck_links_code_entropy
# requiring at least 7.
#
# Alphanumeric, NOT links_service.CODE_ALPHABET, and the difference caused a
# real outage in the lab. CODE_ALPHABET excludes ambiguous glyphs (0/O, 1/l/I)
# because codes get read aloud and retyped -- that is a *generator* policy, a
# statement about what we choose to mint. Compiling it into the *reader* asserts
# something much stronger and false: that no other code can exist. Seeded links
# (test001) and any imported, vanity or legacy code containing a 0 or a 1 became
# permanently unresolvable, 404 before the lookup, with the row sitting in the
# table the whole time. The tell is that changing the generator's alphabet would
# have retroactively broken every link already issued under the old one.
#
# A validator describes what the column can hold. A generator decides what we
# put in it. Those are different questions and only one of them belongs here.
CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{7,32}$")


@router.get("/r/{code}", status_code=status.HTTP_302_FOUND)
def redirect(
    response: Response,
    # No min_length/max_length here on purpose. Declaring them would make a
    # malformed code a 400 while an unknown one is a 404, and that difference is
    # itself an answer: it tells a prober the code format for free, and splits
    # "unresolvable" into two observable outcomes. The regex below applies the
    # same bounds and returns the same 404 as every other way of not resolving.
    code: str = Path(...),
    session: Session = Depends(get_public_session),
) -> Response:
    """302, not 301.

    301 is cached by browsers effectively forever and is not revalidated, so a
    link that is later disabled, expired, or belongs to a suspended tenant keeps
    redirecting for everyone who already visited it -- the kill switch stops
    working for exactly the users who matter. It would also make click counts
    (Module 7) permanently wrong, since a cached redirect never reaches the
    server. 301 is only correct for a destination that is immutable, and every
    field resolve_link() checks is mutable by design.
    """
    if not CODE_PATTERN.match(code):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    resolved = links_service.resolve_code(session, code)
    if resolved is None:
        # One response for every unresolvable reason: unknown code, expired,
        # disabled, suspended tenant. Distinguishing them would confirm which
        # codes exist and leak a tenant's account status to anyone with a URL.
        # The caller gets no more than "no".
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    link_id, long_url = resolved
    enqueue_click(link_id)

    # Location is safe to set from stored data only because url_policy rejected
    # control characters at write time -- a stored CR or LF here would be header
    # injection, letting a create request forge response headers for every
    # visitor of that link. The check that makes this line safe is two modules
    # away from it, which is exactly why it is written down here.
    response.headers["Location"] = long_url

    # Not cacheable. A shared cache holding this response would keep serving a
    # disabled link, and would serve it to a different visitor than the one the
    # click belongs to.
    response.headers["Cache-Control"] = "no-store"
    response.status_code = status.HTTP_302_FOUND
    return response
