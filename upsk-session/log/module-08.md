# Module 08 — Search & Advanced Queries

System Design Fundamentals, **Operator pack** (Practitioner closed 4/4).

## Decision

`decisions.module_08.search_strategy = db_native` — *selected by the instructor
under Himanshu's standing direction to take the recommended option.*

Freshness must be immediate (create a link, find it in the same session), so
eventual consistency is a bug here rather than a trade. The decisive argument is
the Module 06 one, sharpened: **RLS is enforced by Postgres**, so a dedicated
search engine holds a full copy of tenant data outside the isolation boundary,
scoped by application code instead of the database — the cache-versus-RLS
problem again, but permanent and over the whole dataset rather than a 60-second
window. ~2000 links needs no second stateful system.

## Built

- **Migration 0011** — `pg_trgm`, GIN trigram index on `long_url`, GIN on `tags`.
- **`links_service.search_links`** — substring + tag filter, allowlisted sort,
  clamped page size, `(rows, total)`.
- **`GET /links/search`** — declared **before** `/{link_id}`; otherwise FastAPI
  matches `search` against `link_id: uuid.UUID` and answers 422 for a route that
  exists.
- **`LinkSearchPage`** — `page`, `page_size` *as applied*, `total`, `total_pages`.

Three deliberate choices:

- **Sort is the allowlist, and it is the only input that needs one.** A sort
  column is an identifier, not a value, so it is the single place caller input
  would otherwise be concatenated into SQL. `q` and `tag` are bound parameters
  and need no allowlist for injection. Public names (`created`, `clicks`) are
  deliberately not column names, so a rename is not a breaking API change.
- **`page_size` is clamped, `sort` is refused.** A caller asking for 1000 wants
  everything, and 100 rows plus metadata is a working answer where a 422 is an
  error they must handle. There is no nearest sensible value for a sort name
  that does not exist, so that one errors.
- **Wildcards are escaped.** `%` and `_` in the term are escaped and the
  wildcards added server-side, so searching `50%` means the literal characters.

## Why not FTS, and what EXPLAIN actually said

The module suggests `to_tsvector`/`plainto_tsquery`. FTS tokenises natural
language — it stems, drops stop words, matches whole lexemes. A URL is not
natural language, and the requirement is *substring* search: no tsvector matches
`xampl` against `https://example.com`, because that is not a lexeme. FTS would
answer a different question than the endpoint is asked.

**Then the index claim had to be corrected against the live system.** First
draft of the migration said the trigram index is what makes this fast. EXPLAIN
disagreed:

```
-- as postgres, seqscan off, no RLS predicate
Bitmap Index Scan on ix_links_long_url_trgm
      Index Cond: (long_url ~~* '%reports%')        <- index works

-- as upsk_app with app.tenant_id bound: the real path
Seq Scan on links
  Filter: (tenant_id = ... AND long_url ~~* '%reports%')
  Rows Removed by Filter: 1959                     <- and is not used
```

The honest claim is narrower: the index is **usable**, proven, and **not
currently used**. At 1961 rows the planner correctly starts from the tenant
predicate RLS appends to every query and filters the rest.

Kept anyway, and the reason is the interaction: RLS already bounds this scan to
one tenant, so the unindexed case costs a tenant's own rows rather than the
table's. The trigram index stops that bound from mattering once one tenant
outgrows it — at which point the planner switches on its own. Without it the
failure arrives as a gradual slowdown on the largest customer, which is the
hardest kind to attribute.

## Evidence (live, :8099)

```
q=reports                   page=1 size=20  total=2    pages=1   returned=2
q=summary size=2 page=1     total=2 pages=1 returned=2
q=summary size=2 page=2     returned=0                  <- no phantom page
page_size=5000              size reported 100, total 1881, returned 100   <- clamped
tag=m08                     total=3
sort=clicks&direction=asc   200, ordered by rollup total
sort="created; DROP TABLE links--"   400 {"code":"bad_request",
                                      "message":"sort must be one of ['clicks','created']"}
q=%25 (literal percent)     total=0                     <- escaped, not a wildcard
no API key                  401
tenant scoping              search total 1881 of 1961 rows in the table
```

8 controls exit 0 · pytest 20 passed · ruff clean.

## Carry-forward

- No control covers search. The allowlist and the escaping are the parts worth
  watching — both fail silently in the direction of "more results than you
  should see". Belongs in the FIX step or Module 09.
- `total` is a COUNT on every page. Bounded today by the tenant predicate and
  ~1881 rows; it is the first thing that will hurt at scale, and the usual fix
  (drop `total`, return `has_more`) is an API change.

## BREAK / FIX — injection in the tag filter

**Injected.** The tag was formatted into `ARRAY['{tag}']::text[]` instead of
bound, justified in the diff as "a bound parameter cannot carry the ARRAY
constructor" — false; a `text[]` value binds fine.

**Symptom, and why it is worse than "returns everything".** A blind boolean
oracle. Honest `tag=m08` → 3; `tag="x'] OR TRUE --"` → 1881 (the whole tenant).
Then, seeding a team for a *different* tenant:

```
tag="x'] OR EXISTS(SELECT 1 FROM teams WHERE name LIKE 'secret%') --"  -> 1881 (true)
tag="x'] OR EXISTS(SELECT 1 FROM teams WHERE name LIKE 'atlas%')  --"  -> 0    (false)
```

Another tenant's team name, read one character at a time. RLS still scopes
`links`, but the subquery reaches `teams` and `invitations`, which have no
policy — the exact `OR 1=1` cross-tenant read named in VERIFY, made concrete.

**Fixed** by restoring the bound `:tag`.

## Regression tests

`tests/test_search.py`, 6 tests, real Postgres with the tenant bound:

- `test_tag_injection_is_data_not_syntax` — the exact BREAK payload; asserts the
  count does not change. Written as the attack, not a generic apostrophe check:
  an apostrophe test passes against code that concatenates but escapes quotes,
  so only a payload that *would change the row count if interpreted* proves it.
- `test_query_wildcards_are_literal` — `q="100%"` matches one literal row.
- `test_sort_allowlist_refuses_unknown_columns` — three payloads, all `ValueError`.
- `test_page_size_is_clamped_not_rejected`.
- `test_pagination_partitions_the_result_exactly` — reassembles pages, asserts
  every row appears exactly once (the 1-based-page / 0-based-offset bug).
- `test_no_phantom_final_page_when_total_is_a_multiple` — the ceiling bug.

Each test tags its own rows with a per-run unique tag and filters on it, so
counts are exact against the borrowed tenant's 1881 existing links.

**Watched failing:** reintroducing the interpolation flips
`test_tag_injection_is_data_not_syntax` to `assert 1884 == 0`.

## Final state

8 controls exit 0 · pytest **26 passed** (was 20) · ruff clean.
