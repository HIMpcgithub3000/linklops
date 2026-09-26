"""Link operations. Routers stay thin; the decisions live here.

Nothing in this module filters by tenant_id, and that is the point rather than
an omission. The session handed in by app.db.get_session() has already bound
app.tenant_id for the transaction, so row-level security scopes every statement
below whether or not this code remembers to. Adding a redundant WHERE
tenant_id = ? here would look like belt-and-braces and would actually be a
liability: it would make the RLS policy untested by this path, so the day the
policy drifts -- see Module 02 -- the application would keep working and the
isolation failure would surface somewhere else, later, without a control.
"""

import base64
import binascii
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Link, new_id

# Ambiguous glyphs are excluded: these codes get read aloud, retyped from a
# printed page, and dictated over a phone. 0/O and 1/l/I cost support tickets.
CODE_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# 8 characters over a 56-symbol alphabet is ~46 bits. The check constraint
# ck_links_code_entropy enforces >= 7 at the database level; 8 is chosen here
# because possession of the code is the *only* authorization on the public
# redirect, so code entropy is the authentication strength of that endpoint. A
# short code is not a nicer URL, it is a guessable credential.
CODE_LENGTH = 8

# A collision is a lost insert, not a corruption -- the unique index refuses it.
# At 46 bits the retry is effectively never taken, so the loop exists to keep
# the failure mode "one extra round trip" instead of "a 500 the user sees".
CODE_ATTEMPTS = 5

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


def make_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def encode_cursor(created_at: datetime, link_id: uuid.UUID) -> str:
    """Opaque cursor over the sort key.

    Base64 not for secrecy -- it is trivially reversible -- but to make the
    cursor obviously not a hand-editable parameter. A cursor that looks like a
    timestamp invites clients to construct one, and then its exact format is a
    published API that cannot change when the sort key does.
    """
    raw = f"{created_at.isoformat()}|{link_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Reverse encode_cursor. Raises ValueError on anything malformed.

    A cursor is client-supplied, so every failure here is an expected input
    error rather than a bug: it must surface as a 400, never as a 500.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        stamp, _, ident = raw.partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("cursor is not valid") from exc


def create_link(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    long_url: str,
    expires_at: datetime | None,
    tags: list[str] | None,
) -> Link:
    """Insert a link, retrying only a genuine code collision.

    tenant_id is written from the bound tenant, never from the request body --
    LinkCreate forbids the field outright, so there is no path by which a client
    names the owner of a row.
    """
    for attempt in range(CODE_ATTEMPTS):
        link = Link(
            id=new_id(),
            tenant_id=tenant_id,
            created_by=actor_id,
            code=make_code(),
            long_url=long_url,
            expires_at=expires_at,
            tags=tags,
        )
        # A savepoint, because a failed statement aborts the whole Postgres
        # transaction: without this the retry would run inside a poisoned
        # transaction and fail with "current transaction is aborted" rather than
        # with anything about codes.
        savepoint = session.begin_nested()
        try:
            session.add(link)
            session.flush()
        except IntegrityError as exc:
            savepoint.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint != "uq_links_code" or attempt == CODE_ATTEMPTS - 1:
                # Any other constraint is a real bug and must not be retried into
                # silence. Re-raised as-is so the router's handler classifies it.
                raise
            continue
        savepoint.commit()
        return link
    raise RuntimeError("unreachable: the loop returns or raises")


def list_links(
    session: Session,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[Link], str | None]:
    """One page, newest first, ordered to match ix_links_tenant_created.

    The ORDER BY is (created_at DESC, id DESC) because that is the index's
    declared order -- matching it lets the planner walk the index instead of
    sorting, and the trailing id is what makes the order *total*. Without a
    tiebreaker, two links created in the same transaction share a timestamp, and
    a page boundary landing between them either drops one or serves it twice.
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    stmt = select(Link).order_by(Link.created_at.desc(), Link.id.desc())
    if cursor:
        after_created, after_id = decode_cursor(cursor)
        # Row-value comparison, not (created_at < x OR (created_at = x AND ...)).
        # Postgres compares the tuple lexicographically and can match it against
        # the composite index directly.
        stmt = stmt.where(
            (Link.created_at, Link.id) < (after_created, after_id)  # type: ignore[operator]
        )

    # One extra row is fetched purely to answer "is there a next page" without a
    # second COUNT query -- a count over a tenant's whole link table on every
    # page render is a scan the user never asked for.
    rows = session.execute(stmt.limit(limit + 1)).scalars().all()
    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
    return items, next_cursor


def list_links_paged(
    session: Session, *, page: int, limit: int, as_of: datetime
) -> list[Link]:
    """Page-numbered listing for the admin console.

    Two bugs lived here and they were not independent -- one made the wrong rows
    appear, the other made "wrong" unrepeatable.

    offset is (page - 1) * limit because pages are 1-based in the API. With
    page * limit, page 1 asked for offset 10 and the first ten links were
    unreachable through this endpoint entirely: not duplicated, not reordered,
    absent. Nothing errored, and every page looked plausible on its own.

    ORDER BY is not optional on a paginated query. Without it Postgres may
    return rows in any order it likes, and it is free to choose differently
    between two executions of the same statement -- so a row can appear on page
    1 and again on page 2, or on neither, with no concurrent writes required at
    all. LIMIT/OFFSET without ORDER BY does not slice a sequence; it slices
    whatever order the plan happened to produce. Matching the ordering of
    ix_links_tenant_created also lets the planner walk the index instead of
    sorting, and the trailing id makes the order total so equal timestamps
    cannot straddle a page boundary.

    Neither fix is enough on its own, and finding that out cost a second round.
    With the offset corrected and ORDER BY restored, paging while another
    connection inserted still returned 30 rows of which only 23 were distinct,
    in 8 trials out of 8. Ordering was never the problem there -- the query was
    already total and deterministic. The problem is that an offset addresses a
    *position*, and every insert that sorts ahead of what you have already read
    changes which row occupies position N. Newest-first ordering puts every new
    row at position 1, so one insert shifts the entire remainder by one and the
    last row of page N reappears as the first row of page N+1.

    Measured directly against the database, same writer, three orderings:

        newest-first  ORDER BY created_at DESC   duplicates in 5/5 trials
        oldest-first  ORDER BY created_at ASC    duplicates in 0/5 trials
        anchored      DESC + created_at <= as_of duplicates in 0/5 trials

    Oldest-first is stable only because inserts land past the last page, which
    is a property of the data's arrival order rather than of the pagination --
    and it forces the dashboard to show the least interesting links first. So
    the fix is the anchor: as_of freezes the result set at a point in time, and
    rows created after it are not eligible for any page in the run. The client
    is handed the anchor back and returns it with each subsequent page, which
    makes the whole traversal one consistent snapshot instead of three
    independent queries against a moving table.

    Offset is still the wrong tool at depth -- it re-scans every skipped row --
    so list_links() and its cursor remain what the API should prefer. This
    endpoint exists because the admin console wants jump-to-page, which
    genuinely needs positions, and an anchored offset is how a position can mean
    something stable.
    """
    offset = (page - 1) * limit
    stmt = (
        select(Link)
        .where(Link.created_at <= as_of)
        .order_by(Link.created_at.desc(), Link.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars().all())


def get_link(session: Session, link_id: uuid.UUID) -> Link | None:
    """Fetch one link. Returns None both for absent and for another tenant's.

    The two cases are indistinguishable here by construction, because RLS
    filters the row out before this code sees anything. That is the desired
    API behaviour anyway -- a 404 for someone else's id, never a 403, because
    403 confirms the id exists and turns the endpoint into an oracle for
    enumerating other tenants' identifiers.
    """
    return session.get(Link, link_id)


# The sort allowlist. This is the one input in the search API that structurally
# cannot be a bound parameter: a sort column is an identifier, not a value, so
# it is the single place caller input would otherwise be concatenated into SQL.
# Mapping a fixed set of public names to fixed SQL fragments means an unknown
# name is refused rather than interpolated -- and the fragments are literals in
# this file, never built from the request.
#
# The public names are also deliberately not the column names. `clicks` is
# assembled from the analytics rollup, and `created` is `created_at`; keeping
# the API's vocabulary separate from the schema's means a column rename is not
# a breaking API change.
SORT_COLUMNS = {
    "created": "l.created_at",
    "clicks": "COALESCE(a.clicks, 0)",
}
SORT_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}
MAX_QUERY_LENGTH = 128


def search_links(
    session: Session,
    *,
    q: str | None,
    tag: str | None,
    sort: str,
    direction: str,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    """Search a tenant's links. Returns (rows, total_matching).

    Tenant scoping is the database's: every statement here runs on the bound
    session, so the RLS predicate on `links` is appended by Postgres. There is
    deliberately no `WHERE tenant_id = ...` in this function -- adding one would
    imply the isolation depends on remembering to write it.

    The click count comes from the `analytics` rollup rather than from
    click_events, for the same reason the analytics endpoint does: raw events
    are purged on a retention schedule and the rollup is not, so sorting by
    clicks must not silently reorder as the purge runs.

    Two things are bounded before the query is built rather than after:
    `page_size` is clamped, and `q` is truncated. An unbounded search term is a
    cost parameter the caller controls -- a megabyte-long string still has to be
    trigram-matched against every candidate row.
    """
    if sort not in SORT_COLUMNS:
        raise ValueError(f"sort must be one of {sorted(SORT_COLUMNS)}")
    if direction not in SORT_DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(SORT_DIRECTIONS)}")

    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    params: dict[str, object] = {}
    where = ["TRUE"]

    if q:
        # ILIKE with a bound parameter. The wildcards are added here rather than
        # by the caller so that a caller cannot supply their own pattern -- '%'
        # and '_' inside the term are escaped below, so a search for "50%" means
        # the literal characters and not "anything".
        term = q.strip()[:MAX_QUERY_LENGTH]
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("l.long_url ILIKE :q ESCAPE '\\'")
        params["q"] = f"%{escaped}%"

    if tag:
        # Containment against the GIN index on tags, not `= ANY`, so the index
        # is usable. Normalised the same way create does, or a tag stored as
        # "Reports" is unfindable by "reports".
        where.append("l.tags @> ARRAY[:tag]::text[]")
        params["tag"] = tag.strip()

    predicate = " AND ".join(where)

    # `clicks` is a correlated aggregate rather than a join to avoid multiplying
    # link rows by rollup rows before the LIMIT. The subquery reads `analytics`,
    # which carries its own RLS policy scoped through links (migration 0010), so
    # it cannot see another tenant's counts even here.
    base = f"""
        FROM links l
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(count), 0) AS clicks
            FROM analytics an WHERE an.link_id = l.id
        ) a ON TRUE
        WHERE {predicate}
    """  # noqa: S608 - `predicate` is assembled from literals above, never from input

    total = session.execute(text(f"SELECT COUNT(*) {base}"), params).scalar() or 0  # noqa: S608

    order = f"{SORT_COLUMNS[sort]} {SORT_DIRECTIONS[direction]}"
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = session.execute(
        text(
            f"""
            SELECT l.id, l.code, l.long_url, l.created_at, l.expires_at, l.tags,
                   COALESCE(a.clicks, 0) AS clicks
            {base}
            -- id DESC is the tiebreaker that makes the order total. Without it
            -- two links sharing a created_at, or two links with equal click
            -- counts, can swap places between two executions of the same query
            -- -- and a row that moves across a page boundary is served twice or
            -- not at all. This is the same bug the cursor listing was fixed for.
            ORDER BY {order}, l.id DESC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608 - `order` comes from the allowlist above
        ),
        params,
    ).all()

    return [dict(row._mapping) for row in rows], int(total)


def update_link(
    session: Session,
    link_id: uuid.UUID,
    *,
    long_url: str | None = None,
    disabled: bool | None = None,
) -> Link | None:
    """Mutate a link's resolution inputs. Returns None if the tenant cannot see it.

    Reached through the tenant-bound session, so a link belonging to another
    tenant is simply not found -- the same shape as get_link, and the reason
    this needs no ownership check of its own.

    Deliberately does NOT invalidate the cache. That call belongs to the router,
    after the transaction commits: invalidating here would drop the cache entry
    while the new value is still uncommitted, so a concurrent redirect could
    read the *old* row from the database and refill the cache with it. The
    result is a cache that is stale precisely because it was invalidated, which
    is the kind of bug that survives a long time because the invalidation is
    right there in the diff.
    """
    link = get_link(session, link_id)
    if link is None:
        return None

    if long_url is not None:
        link.long_url = long_url
    if disabled is not None:
        link.disabled_at = datetime.now(UTC) if disabled else None

    session.flush()
    return link


def resolve_code(session: Session, code: str) -> tuple[uuid.UUID, str] | None:
    """Public redirect lookup, through the one audited tenant-free read.

    resolve_link() is SECURITY DEFINER and applies the resolvability rules
    itself -- tenant_active, not disabled, not expired -- so those rules cannot
    drift apart from the redirect path by being reimplemented here.

    Returns (link_id, long_url). The id is needed for the analytics enqueue and
    is deliberately not exposed to the caller -- it is an internal identifier
    that happens to travel with the redirect, not part of the redirect's
    contract.
    """
    row = session.execute(
        text("SELECT link_id, long_url FROM resolve_link(:code)"), {"code": code}
    ).first()
    return (row[0], row[1]) if row else None
