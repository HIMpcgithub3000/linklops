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
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import Link
from app.schemas.link import LinkCreate, LinkOut, LinkPage
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
    session: Session = Depends(get_session),
) -> LinkOut:
    link = links_service.create_link(
        session,
        tenant_id=uuid.UUID(request.headers["X-Dev-Tenant-Id"]),
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
    session: Session = Depends(get_session),
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


@router.get("/{link_id}", response_model=LinkOut)
def get_link(link_id: uuid.UUID, session: Session = Depends(get_session)) -> LinkOut:
    link = links_service.get_link(session, link_id)
    if link is None:
        # 404 for another tenant's id as well as for a nonexistent one. A 403
        # would confirm the id exists, which turns this endpoint into an oracle
        # for enumerating identifiers across tenants -- the answer is
        # information even when the body is not.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="link not found")
    return to_out(link)
