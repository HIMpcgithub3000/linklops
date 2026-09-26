"""Management-plane routes. Tenant-scoped, and thin by construction.

Every route here depends on get_session, which binds app.tenant_id for the
transaction before the handler runs. The dependency *is* the authorization: a
handler that forgets to filter returns zero rows rather than everyone's, so the
failure mode of forgetting is empty output, not a leak.

The pairing with get_public_session is the thing to get right. That session is
deliberately untenanted and can read nothing from links; wiring a management
route to it by mistake yields an empty list rather than an error, which reads to
a developer as "no data yet" instead of "wrong dependency". Hence: one session
dependency per plane, named for its plane, never defaulted.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app import cache, ratelimit
from app.auth import get_tenant_session, require_principal
from app.config import settings
from app.models import Link
from app.schemas.link import (
    LinkAnalytics,
    LinkCreate,
    LinkOut,
    LinkPage,
    LinkSearchPage,
    LinkSearchResult,
    LinkUpdate,
)
from app.services import links_service

router = APIRouter(prefix="/links", tags=["links"])

# Attribution until Module 4 lands real authentication. The nil UUID is chosen
# over a random placeholder because it is unmistakably synthetic and greppable:
# a real actor id appearing here later is a change someone made on purpose, and
# a nil surviving into a deployment is obvious on sight. Safe as a placeholder
# only because created_by is attribution and must never appear in an
# authorization predicate -- see the comment on the column in app/models.py.
DEV_UNATTRIBUTED_ACTOR = uuid.UUID(int=0)
DEV_ACTOR_HEADER = "X-Dev-Actor-Id"


def resolve_actor_id(request: Request) -> uuid.UUID:
    raw = request.headers.get(DEV_ACTOR_HEADER)
    if not raw:
        return DEV_UNATTRIBUTED_ACTOR
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{DEV_ACTOR_HEADER} must be a UUID",
        ) from exc


def to_out(link: Link) -> LinkOut:
    """Build the response, including the short_url the caller should hand out.

    The origin comes from configuration, never from the request -- a Host header
    is client-supplied, so deriving it there would let a caller be handed a
    short_url on their own domain for a real code in this database.
    """
    base = str(settings.PUBLIC_BASE_URL).rstrip("/")
    return LinkOut(
        id=link.id,
        code=link.code,
        short_url=f"{base}/r/{link.code}",
        long_url=link.long_url,
        created_at=link.created_at,
        expires_at=link.expires_at,
        tags=link.tags,
    )


@router.post("", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
def create_link(
    payload: LinkCreate,
    request: Request,
    principal: str = Depends(require_principal),
    session: Session = Depends(get_tenant_session),
) -> LinkOut:
    ratelimit.check("create", principal)
    link = links_service.create_link(
        session,
        tenant_id=uuid.UUID(principal),
        actor_id=resolve_actor_id(request),
        long_url=payload.long_url,
        expires_at=payload.expires_at,
        tags=payload.tags,
    )
    session.flush()
    session.refresh(link)
    return to_out(link)


@router.get("", response_model=LinkPage)
def list_links(
    principal: str = Depends(require_principal),
    session: Session = Depends(get_tenant_session),
    limit: int = Query(links_service.DEFAULT_PAGE_SIZE, ge=1, le=links_service.MAX_PAGE_SIZE),
    cursor: str | None = Query(None),
    page: int | None = Query(None, ge=1),
    as_of: datetime | None = Query(None),
) -> LinkPage:
    if page is not None:
        # First page of a run establishes the snapshot; later pages carry it
        # back. Defaulting to now() only when absent means a client that ignores
        # as_of entirely still works -- it just gets the old drifting behaviour,
        # which is the right failure mode for a parameter nobody has adopted yet.
        anchor = as_of or datetime.now(UTC)
        items = links_service.list_links_paged(session, page=page, limit=limit, as_of=anchor)
        return LinkPage(items=[to_out(link) for link in items], next_cursor=None, as_of=anchor)
    try:
        items, next_cursor = links_service.list_links(session, limit=limit, cursor=cursor)
    except ValueError as exc:
        # A malformed cursor is client input, not a server fault. Returning 500
        # here would page someone for a typo in a query string.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return LinkPage(items=[to_out(link) for link in items], next_cursor=next_cursor)


# DECLARED BEFORE /{link_id}. FastAPI matches routes in declaration order, so
# with this below the parameterised route, GET /links/search is matched against
# `link_id: uuid.UUID`, fails validation, and answers 422 for a route that
# exists. A literal segment that collides with a path parameter is a routing
# bug, not a validation one, and the fix is ordering rather than a special case
# inside the handler.
@router.get("/search", response_model=LinkSearchPage)
def search_links(
    q: str | None = Query(None, max_length=links_service.MAX_QUERY_LENGTH),
    tag: str | None = Query(None, max_length=64),
    sort: str = Query("created"),
    direction: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(links_service.DEFAULT_PAGE_SIZE, ge=1),
    principal: str = Depends(require_principal),
    session: Session = Depends(get_tenant_session),
) -> LinkSearchPage:
    """Search this tenant's links by URL substring and tag.

    `page_size` has `ge=1` but deliberately no `le=`: it is *clamped* to
    MAX_PAGE_SIZE in the service rather than rejected. A caller asking for 1000
    wants everything, and 100 rows plus the metadata to ask for more is a
    working answer, where a 422 is an error they have to write code to handle.
    The cap is still absolute -- the difference is only in how it is delivered,
    and the response reports the size actually applied so the caller is never
    misled about what they got.

    `sort` and `direction` are refused rather than clamped, because there is no
    nearest sensible value for a name that does not exist -- silently sorting by
    something the caller did not ask for is worse than telling them.
    """
    try:
        rows, total = links_service.search_links(
            session, q=q, tag=tag, sort=sort, direction=direction,
            page=page, page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    base = str(settings.PUBLIC_BASE_URL).rstrip("/")
    applied = max(1, min(page_size, links_service.MAX_PAGE_SIZE))
    return LinkSearchPage(
        items=[
            LinkSearchResult(
                id=row["id"],
                code=row["code"],
                short_url=f"{base}/r/{row['code']}",
                long_url=row["long_url"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                tags=row["tags"],
                clicks=int(row["clicks"]),
            )
            for row in rows
        ],
        page=page,
        page_size=applied,
        total=total,
        # Computed rather than inferred from a full page: "the last page was
        # full, so try another" is the off-by-one that produces an empty final
        # page when total is an exact multiple of page_size.
        total_pages=(total + applied - 1) // applied,
    )


@router.get("/{link_id}/analytics", response_model=LinkAnalytics)
def link_analytics(
    link_id: uuid.UUID,
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    principal: str = Depends(require_principal),
    session: Session = Depends(get_tenant_session),
) -> LinkAnalytics:
    """Click totals for one link over a window.

    Reads the `analytics` rollup, not `click_events`, and that is a retention
    decision rather than a performance one. Raw events are purged after
    CLICK_RETENTION_DAYS; the rollup is aggregate, carries no per-visitor row,
    and is kept. Counting raw events would mean this endpoint's answer silently
    shrinking as the purge caught up with the window -- a number that changes
    when nothing happened.

    Tenant scoping is the database's, not this handler's. Migration 0010 put a
    policy on `analytics` scoped through links, so this query returns nothing
    for another tenant's link even though the handler never mentions a tenant.
    The 404 below is therefore the same answer for "no such link" and "not
    yours", which is the rule the rest of this router already follows.

    The window is half-open [from, to): a click at exactly `to` belongs to the
    next window, so two adjacent windows partition the timeline instead of
    double-counting the instant they share.
    """
    if to <= from_:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`to` must be after `from`",
        )

    # The existence check is separate and comes first. Without it, a link with
    # no clicks and a link belonging to another tenant would both produce a zero
    # row -- reporting 0 clicks for a link the caller may not know exists is a
    # weaker answer than 404, and reporting 404 for a real link with no traffic
    # would be wrong.
    if links_service.get_link(session, link_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="link not found")

    row = session.execute(
        text(
            """
            SELECT COALESCE(SUM(count), 0) AS clicks,
                   MAX(last_accessed_at)   AS last_clicked_at
            FROM analytics
            WHERE link_id = :link_id
              AND timestamp_bucket >= :from_ts
              AND timestamp_bucket <  :to_ts
            """
        ),
        {"link_id": str(link_id), "from_ts": from_, "to_ts": to},
    ).one()

    return LinkAnalytics(
        link_id=link_id,
        from_=from_,
        to=to,
        clicks=int(row.clicks),
        last_clicked_at=row.last_clicked_at,
    )


@router.patch("/{link_id}", response_model=LinkOut)
def update_link(
    link_id: uuid.UUID,
    body: LinkUpdate,
    principal: str = Depends(require_principal),
    session: Session = Depends(get_tenant_session),
) -> LinkOut:
    """Change a link's destination or disable it, then drop it from the cache.

    The ordering is the whole substance of this handler and it is the opposite
    of the obvious one. The cache entry is invalidated *after* the transaction
    commits, not alongside the write, because invalidating first opens a window
    in which the row still holds the old value: a redirect arriving inside that
    window misses the cache, reads the old row, and refills the cache with it --
    leaving a stale entry created by the act of invalidation, which then lives
    for a full TTL. Delete-after-commit closes that; the exposure it trades for
    is the opposite window, where the cache serves the old destination for the
    few milliseconds between commit and delete, which self-heals.

    get_tenant_session commits when the handler returns, so the invalidation
    cannot sit inside the handler body. It is registered as a callback on the
    session's after_commit event instead -- the point at which the new row is
    the one any reader would see.
    """
    link = links_service.update_link(
        session, link_id, long_url=body.long_url, disabled=body.disabled
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="link not found")

    code = link.code
    out = to_out(link)

    @event.listens_for(session, "after_commit", once=True)
    def _drop_cache(_: Session) -> None:
        cache.invalidate_redirect_target(code)

    return out


@router.get("/{link_id}", response_model=LinkOut)
def get_link(
    link_id: uuid.UUID,
    principal: str = Depends(require_principal),
    session: Session = Depends(get_tenant_session),
) -> LinkOut:
    link = links_service.get_link(session, link_id)
    if link is None:
        # 404 for another tenant's id as well as for a nonexistent one. A 403
        # would confirm the id exists, which turns this endpoint into an oracle
        # for enumerating identifiers across tenants -- the answer is
        # information even when the body is not.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="link not found")
    return to_out(link)
