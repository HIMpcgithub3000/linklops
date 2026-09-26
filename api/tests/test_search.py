"""Search: injection resistance, the sort allowlist, and pagination boundaries.

The BREAK for this module was an injection in the tag filter -- the tag formatted
into an `ARRAY['...']` literal instead of bound. The symptom was not "returns
everything": it was a blind boolean oracle. `tag=x'] OR EXISTS(SELECT 1 FROM
teams WHERE name LIKE 'secret%') --` returned the whole tenant when true and
nothing when false, reading another tenant's rows one character at a time
through tables RLS does not cover.

So the first test is written as that attack, not as a generic apostrophe check.
An apostrophe test passes against code that concatenates but happens to escape
quotes; only a payload that would *change the row count if interpreted* proves
the input stayed data.

The pagination tests target the two off-by-ones the module names: 1-based pages
fed to a 0-based offset, and total_pages inferred from a full last page (which
is wrong exactly when total is a multiple of page_size).

Every test runs against real Postgres with the tenant bound, so the tenant
predicate RLS appends is in the path -- a mock would assert the mock. Each test
tags its own rows with a per-run unique tag and filters on it, so the counts are
exact regardless of what the borrowed tenant already contains.
"""

import uuid

import pytest
from sqlalchemy import text

from app.services import links_service as svc


@pytest.fixture()
def tenant(db, two_tenants):
    """A dedicated tenant, bound for the transaction.

    Reuses the session-scoped `two_tenants` fixture (which creates real tenants
    on the migration role and cleans them up) rather than borrowing whatever
    tenant happens to be present. The earlier version selected `any existing
    tenant`, which made these tests skip on a fresh test database and pass only
    when some other test had left one around -- order-dependent skipping, which
    is the same flake this module is about, one notch quieter.
    """
    tid, _ = two_tenants
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
    return tid


@pytest.fixture()
def run_tag():
    """A tag no other data carries, so this test's counts are exact."""
    return "pyt-" + uuid.uuid4().hex[:12]


def _seed(db, tenant, run_tag, n, *, prefix="https://seed.example/"):
    for i in range(n):
        db.execute(
            text(
                """
                INSERT INTO links (id, tenant_id, created_by, code, long_url, tags)
                VALUES (:i, :t, :t, :c, :u, ARRAY[:tag]::text[])
                """
            ),
            {
                "i": str(uuid.uuid4()), "t": str(tenant),
                "c": f"pyt{uuid.uuid4().hex[:9]}",
                "u": f"{prefix}{i:03d}", "tag": run_tag,
            },
        )
    db.flush()


def test_tag_injection_is_data_not_syntax(db, tenant, run_tag):
    """The exact BREAK payload must not change the result set."""
    _seed(db, tenant, run_tag, 3)

    _, honest_total = svc.search_links(
        db, q=None, tag=run_tag, sort="created", direction="desc", page=1, page_size=50
    )
    assert honest_total == 3

    # If the tag were interpolated, this OR TRUE would return every row in the
    # tenant; bound, it is a literal tag value that simply matches nothing.
    _, injected_total = svc.search_links(
        db, q=None, tag=f"{run_tag}'] OR TRUE --", sort="created",
        direction="desc", page=1, page_size=50,
    )
    assert injected_total == 0, "the tag filter interpreted input as SQL"


def test_query_wildcards_are_literal(db, tenant, run_tag):
    """A `%` in q matches a literal percent sign, not 'anything'."""
    _seed(db, tenant, run_tag, 3, prefix="https://plain.example/")
    db.execute(
        text(
            """
            INSERT INTO links (id, tenant_id, created_by, code, long_url, tags)
            VALUES (:i, :t, :t, :c, 'https://has.example/100%-off', ARRAY[:tag]::text[])
            """
        ),
        {
            "i": str(uuid.uuid4()), "t": str(tenant),
            "c": f"pyt{uuid.uuid4().hex[:9]}", "tag": run_tag,
        },
    )
    db.flush()

    _, only_literal = svc.search_links(
        db, q="100%", tag=run_tag, sort="created", direction="desc", page=1, page_size=50
    )
    assert only_literal == 1, "the % was treated as a wildcard instead of a literal"


def test_sort_allowlist_refuses_unknown_columns(db, tenant):
    for bad in ["long_url", "created; DROP TABLE links--", "clicks); DELETE FROM links--"]:
        try:
            svc.search_links(db, q=None, tag=None, sort=bad, direction="desc", page=1, page_size=10)
        except ValueError:
            continue
        raise AssertionError(f"sort={bad!r} was accepted instead of refused")


def test_page_size_is_clamped_not_rejected(db, tenant, run_tag):
    _seed(db, tenant, run_tag, 5)
    rows, total = svc.search_links(
        db, q=None, tag=run_tag, sort="created", direction="desc", page=1, page_size=10_000
    )
    assert total == 5
    assert len(rows) <= svc.MAX_PAGE_SIZE


def test_pagination_partitions_the_result_exactly(db, tenant, run_tag):
    """Every row appears on exactly one page -- none dropped, none doubled.

    This is the 1-based-page / 0-based-offset bug: with offset = page * size,
    page 1 skips the first `size` rows and they are unreachable. Asserted by
    reassembling the pages and comparing to the whole set.
    """
    _seed(db, tenant, run_tag, 7)
    size = 3
    seen: list[uuid.UUID] = []
    for page in range(1, 5):  # 7 rows / 3 = 3 pages, plus one past the end
        rows, total = svc.search_links(
            db, q=None, tag=run_tag, sort="created", direction="desc", page=page, page_size=size
        )
        assert total == 7
        seen.extend(r["id"] for r in rows)

    assert len(seen) == 7, f"pages returned {len(seen)} rows for 7 links"
    assert len(set(seen)) == 7, "a row appeared on more than one page"


def test_no_phantom_final_page_when_total_is_a_multiple(db, tenant, run_tag):
    """total_pages must be computed, not inferred from a full last page.

    With exactly 6 rows and page_size 3 there are 2 pages. The 'last page was
    full, so try another' heuristic would invent a third, empty page.
    """
    _seed(db, tenant, run_tag, 6)
    size = 3
    total = svc.search_links(
        db, q=None, tag=run_tag, sort="created", direction="desc", page=1, page_size=size
    )[1]
    total_pages = (total + size - 1) // size
    assert total_pages == 2

    rows, _ = svc.search_links(
        db, q=None, tag=run_tag, sort="created", direction="desc",
        page=total_pages + 1, page_size=size,
    )
    assert rows == [], "there is a page beyond the computed last one"
