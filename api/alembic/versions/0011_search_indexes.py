"""M8: indexes for link search

Two indexes, and the choice of the first is a deliberate departure from the
module's suggestion of full-text search. Written down because "we used trigrams
where the template said tsvector" is exactly the kind of decision that looks
like carelessness a year later.

**Why not FTS on long_url.** Full-text search tokenises natural language: it
stems words, drops stop words, and matches whole lexemes. A URL is not natural
language. Postgres' default parser does split `https://example.com/reports/q3`
into host and path tokens, so `plainto_tsquery('example.com')` matches -- but
the requirement is *substring* search, and no amount of tsvector matches `xampl`
or `reports/q` against that URL, because those are not lexemes. FTS would answer
a different question than the one the endpoint is asked.

**Why trigrams.** pg_trgm indexes every three-character sequence, which is what
makes `long_url ILIKE '%xampl%'` indexable at all. A leading-wildcard LIKE is
otherwise unservable by any btree and degrades to a sequential scan -- the
"unbounded query took down the primary" incident the module describes, reached
not by a missing LIMIT but by a predicate no index can satisfy.

**What EXPLAIN actually says today, which is not what I first wrote here.**
Verified against the live database rather than assumed:

    -- as postgres, seqscan disabled, no RLS predicate in play
    Bitmap Heap Scan on links
      Recheck Cond: (long_url ~~* '%reports%')
      ->  Bitmap Index Scan on ix_links_long_url_trgm
            Index Cond: (long_url ~~* '%reports%')     <- the index works

    -- as upsk_app with app.tenant_id bound: the real query path
    Seq Scan on links
      Filter: (tenant_id = ... AND long_url ~~* '%reports%')
      Rows Removed by Filter: 1959                     <- and is not used

So the honest claim is narrower than "this index makes search fast". At 1961
rows the planner correctly prefers to start from the tenant predicate that
row-level security appends to every query and filter the rest, because scanning
one tenant's 1881 rows is cheaper than a trigram lookup plus recheck. The index
is proven *usable* and is not currently *used*.

That is worth keeping rather than deleting, and the reason is the interaction
itself: RLS already bounds this scan to one tenant, so the unindexed case costs
a tenant's own rows and not the table's. The trigram index is what stops that
bound from mattering once a single tenant's link count outgrows it -- at which
point the planner will switch on its own. Removing it would leave nothing to
switch to, and the failure would arrive as a gradual slowdown on the largest
customer, which is the hardest kind to attribute.

The tags index is a plain GIN on the array, which serves the containment
operator `tags @> ARRAY[...]` used by the tag filter.

Both are unqualified by tenant_id, deliberately. Row-level security appends the
tenant predicate to every query, so the planner combines this index with the
tenant filter itself; a composite (tenant_id, trigram) index is not expressible
anyway, since GIN operator classes differ per column.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Shipped with Postgres as a standard contrib module, not a third-party
    # dependency -- but still an extension, so it is created here rather than
    # assumed. A migration that assumes an extension fails on a fresh database
    # with a message about a missing operator class, which names the symptom
    # and not the cause.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_links_long_url_trgm "
        "ON links USING gin (long_url gin_trgm_ops)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_links_tags ON links USING gin (tags)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_links_tags")
    op.execute("DROP INDEX IF EXISTS ix_links_long_url_trgm")
    # The extension is deliberately NOT dropped: another table may have started
    # using it, and dropping an extension cascades to every dependent object.
