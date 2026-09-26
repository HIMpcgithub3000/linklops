# Module 06 — Caching With Redis

System Design Fundamentals, practitioner pack.

## Decisions

- `decisions.module_06.cache_strategy = cache_aside`
- `decisions.module_06.constraints_twist_type = org_scoping`
- `decisions.module_06.constraints_twist_action = implement_now`

**Why cache-aside.** Not the stale window — explicit invalidation closes that
either way. The deciding factor is where Redis sits relative to the *write*
path. Write-through puts it inside that path, so a Redis outage forces a choice
between failing writes and letting cache and database diverge silently.
Cache-aside keeps Redis strictly optional, which is the property `main.py`
`ready()` already committed to when it deliberately excluded Redis from
readiness: a cache with a database fallback may degrade latency, never
availability. At a ~1000:1 read ratio, write-through also pays a cache write on
every write to buy a window that is already closed.

**Why implement the twist now.** The risk is narrower than "put tenant_id in
the keys", and sharper:

> RLS is enforced by Postgres. A cache hit never reaches Postgres. Every cached
> read is therefore a read with RLS switched off, and the key is the only thing
> scoping it to a tenant.

`/r/{code}` is *not* the exposure — it is public, codes are globally unique
(`uq_links_code`), and `tenant_id` is an output of resolution. The exposure is
the management plane: any cache over `list_links` or click analytics bypasses
the isolation mechanism the whole architecture rests on, and the failure is
silent from every angle — well-formed response, normal logs, innocent database,
and `check_rls.py` blind to it because no query ran.

## Built

`app/cache.py` — cache-aside helpers plus the key discipline.

- `key(namespace, *parts, tenant_id=...)` refuses to build a key in a
  tenant-scoped namespace without a tenant, refuses a tenant in a global one
  (silent fragmentation hides the disagreement), and refuses unregistered
  namespaces outright. `CacheKeyError` is the one failure in this module that is
  **not** soft: a missing cache is a slower service, a missing tenant in a key
  is a leak.
- `NAMESPACES` registers the scoping decision *and its reason* once. `redirect`
  is registered global with the reason written next to it — an unexplained
  exception is indistinguishable from a bug. `links_page` and `click_totals` are
  registered tenant-scoped although nothing populates them yet, because the
  enforcement is only worth anything if it exists before the first tenant-scoped
  cache does.
- Every Redis call fails soft. A miss and an outage return the same value, so no
  caller can grow a branch that treats one as fatal.
- `fill_once()` — single-flight around a miss, the module's named incident. One
  miss produces one load; losers wait at most 50ms for the winner and then read
  the database themselves. The bound matters more than the saving: a redirect
  that blocks on a lock has traded an availability property for a load property.
- TTL 60s + up to 10s jitter. Jitter because a set of keys minted in one burst
  expiring in one instant is how a cache converts a traffic spike into a
  database spike.

`app/routers/redirect.py` — wired cache-aside, plus a `cache: hit|miss` field on
the resolution log line. That field is what makes the cache claim checkable at
all: the response is byte-identical either way.

`app/routers/links.py` — added `PATCH /links/{link_id}` (destination change and
disable), which is what makes invalidation demonstrable.

`app/schemas/link.py` — `LinkUpdate`, reusing the create validator. A
destination policy enforced on create but not on update is not a policy, it is a
delay: create something innocuous, then PATCH it to the payload you wanted.

## Two orderings that are easy to get backwards

1. **Invalidate after commit, not with the write.** Invalidating alongside the
   write opens a window where the row still holds the old value — a redirect
   arriving inside it misses, reads the old row, and refills the cache with it.
   The result is an entry that is stale *because* it was invalidated. Registered
   on the session's `after_commit` event instead.
2. **The service does not invalidate; the router does.** Same reason, stated
   where someone would otherwise "helpfully" add the call.

## Staleness, stated honestly

| transition | invalidation | worst case |
|---|---|---|
| destination changed | explicit, after commit | immediate |
| disabled | explicit, after commit | immediate |
| deleted | explicit, after commit | immediate |
| **time-based expiry** | **none possible** | **up to 70s** |

Expiry is evaluated at read time by `resolve_link()` and no job fires when a
link expires, so there is nothing to hook an invalidation onto. TTL is the only
bound. The trade: this cache slightly blunts a time-based expiry and does not
blunt the kill switch at all — the right direction, but it is a real cost and
not a rounding error.

Negative results are not cached. An unknown code is usually a prober, so caching
negatives spends memory on hostile traffic, and the one legitimate case — a code
created moments ago — is exactly what a negative entry would break.

## Evidence (live, API on :8099, `linkops-postgres` :5433, `linkops-redis` :6379)

```
1. first redirect     302 -> https://example.com/1        cache: miss
   redis GET redirect:test001  {"id": "019ffbf9-…", "url": "https://example.com/1"}
   redis TTL                   64            (60 + jitter)
2. second redirect    302 -> https://example.com/1        cache: hit
3. PATCH /links/{id}  200, long_url -> https://example.org/updated
   redis GET redirect:test001  (empty)       invalidated after commit
4. redirect           302 -> https://example.org/updated  cache: miss   no stale serve
5. docker stop linkops-redis, API left running:
   redirect 1  302 -> …/updated  5.5ms
   redirect 2  302 -> …/updated  4.7ms
   redirect 3  302 -> …/updated  6.5ms
   logs: "cache read failed, serving from database" / "cache write failed" (warning)
   /ready = 200 throughout — Redis is deliberately not a readiness dependency
```

## Standing control added

`api/scripts/check_cache_scoping.py` — sixth in the family. Two halves:

- **the rule** — tenant-scoped namespaces refuse an unscoped key, global ones
  refuse a scoped one, unregistered namespaces are refused, two tenants never
  collide, and every namespace must carry a stated reason for its scoping.
- **the reach** — the rule is worthless if code can skip it, so the source is
  scanned for direct `redis_client.client.*` calls outside a justified
  allowlist (`cache.py`, `redis_client.py`, `redirect.py`'s analytics queue).

**Watched failing:**

| reintroduced | exit | reported |
|---|---|---|
| tenant guard removed from `key()` | 1 | `links_page:t:None:x`, `click_totals:t:None:x` |
| a service builds its own Redis key | 1 | `links_service.py:271 calls Redis directly, bypassing key()` |

No database, no Redis, no server — belongs in CI on every commit.

## Full state at close

`check_cache_scoping` 0 · `check_log_pii` 0 · `check_log_injection` 0 ·
`check_url_policy` 0 (43 cases) · `check_rls` 0 · `audit_stored_destinations` 0
(1958 links) · pytest 20 passed · ruff clean.

## Carry-forward

- **No automated test covers the cache.** The evidence above is a live drill run
  by hand; `check_cache_scoping.py` covers key discipline, not hit/miss/
  invalidate behaviour. That gap belongs in Module 09 (Testing) at the latest.
- **`fill_once()` single-flight is not load-tested.** The logic is correct by
  inspection and exercised on every miss, but the stampede it exists to prevent
  has never been reproduced here, so the claim is "implemented", not "proven".
- Three controls still not in `ci.yml`: `check_cache_scoping.py`,
  `check_log_pii.py`, `check_tenant_coverage.py` (the last still exits 1 —
  `teams`, `team_memberships`, `invitations` have no RLS).
- An API key labelled `m06-evidence` was minted for tenant `019ffbf9-…e86d`.
  Revoke it when the evidence runs are done.

## VERIFY — the three questions

**Why is cache invalidation hard?** Not because deleting a key is hard. Three
reasons, all visible in this module's build:

1. *It is a second write to a second system with no shared transaction.* The
   database commit and the Redis delete cannot both succeed or both fail, so
   there is always an ordering, and both orderings are wrong in different ways
   (see the after-commit note above). The best available answer is to pick the
   window that self-heals over the window that persists for a full TTL.
2. *Not every transition has an event to hook.* Destination change, disable and
   delete all have one. Time-based expiry does not — `resolve_link()` evaluates
   it at read time and no job fires — so for that transition invalidation is
   simply impossible and TTL is the entire mechanism.
3. *The failure is invisible.* A missed invalidation returns a valid response.
   Nothing errors, nothing is logged unless the delete itself failed. That is
   why `invalidate_redirect_target()` logs at ERROR rather than WARNING: a
   failed read costs latency, a failed invalidation means the cache is knowingly
   serving something the database no longer says.

**Risk of caching auth-protected data?** It is the whole reason the twist was
implemented now rather than deferred. Authorization here is row-level security,
enforced by Postgres, and a cache hit never reaches Postgres — so a cached read
is a read with authorization switched off, and the key is the only thing left
scoping it. Two failure shapes: a key missing its tenant serves one tenant's
data to another, and a key that is correct but whose *contents* were fetched
under a different principal serves data the current caller could not have read.
Both return well-formed responses, and `check_rls.py` cannot see either, because
no query ran for it to inspect. Hence `key()` refusing to construct an unscoped
key at all — the check has to happen at construction, since there is no later
point where the mistake is still visible.

**What if Redis is unavailable?** Serve from the database, degraded and slower,
never failing — and this was already decided before this module: `ready()`
deliberately excludes Redis so an outage cannot pull the fleet out of rotation
for a dependency the user-facing path does not need. Implemented as fail-soft on
every call, with a miss and an outage returning the same value so no caller can
branch on it. Drilled live: Redis stopped, three redirects still 302 in ~5ms,
`/ready` still 200, warnings logged for the read and write failures. What is
*lost* during the outage is stated rather than hidden — analytics clicks are
dropped (logged per request), and every request pays a database read.

## BREAK / FIX — the thundering herd

**Injected.** `fill_once()`'s loser path was changed to read the database
immediately instead of waiting briefly for the winner's fill. The diff justified
it as a latency optimisation, and every clause of that justification is true:
polling costs up to 50ms, the database read is one indexed function call, and
the winner still populates the cache for whoever comes next. The conclusion is
still wrong — if losers read through, N concurrent misses on one key produce N
database reads, which is the stampede the function exists to stop.

**Symptom.** Nothing visible from outside. Every response was a clean 302.

```
                        concurrent requests   database reads
single-flight intact             20                 1
loser reads through              20                20        (32-58ms each)
```

Counted with a new `redirect cache fill` log line — one per actual database
read. `cache: miss` cannot show this: under a stampede *every* request misses,
and the question is how many of those misses reached Postgres.

**Fixed** by restoring the bounded wait, with the measurement written into the
comment so the next person to see the optimisation has the number in front of
them. The bound is what makes waiting safe: capped at `FILL_WAIT_SECONDS`, ends
in a database read rather than an error, so the worst case is a slightly slower
redirect and never one that hangs on a lock. Re-measured live after the fix:
20 concurrent requests at a cold key, **1** database read.

## Second standing control

`api/scripts/check_cache_behaviour.py` — seventh in the family, and the one that
retires this module's carry-forward before it was written down. `check_cache_
scoping.py` watches the keys; this watches the behaviour, which until now was
proven only by a hand-run drill recorded in this file. A drill nobody re-runs is
a claim, not a control.

Four properties, each with a failure that returns a correct redirect anyway:

| claim | broken means |
|---|---|
| fill and hit | the cache silently never fills; every request pays a database read |
| invalidation | a changed destination, or a disabled link, keeps serving for a TTL |
| single flight | the herd — faster in tests, database absorbs the first hot-key expiry under load |
| fail soft | an optional dependency has become a required one |

It refuses to run without Redis rather than skipping: `fill_once()` treats every
caller as the lock winner when Redis is unreachable, so the single-flight check
would otherwise pass for the wrong reason. No database and no server — the
loader is a counting stub with a deliberate delay, since without the delay the
winner finishes before the losers start and a broken implementation would pass.

**Watched failing:**

| reintroduced | exit | reported |
|---|---|---|
| loser reads through | 1 | `24 concurrent misses produced 24 loads, not 1` |
| invalidation is a no-op | 1 | `the entry survived invalidation` |

## Final state

`check_cache_behaviour` 0 · `check_cache_scoping` 0 · `check_log_pii` 0 ·
`check_log_injection` 0 · `check_url_policy` 0 · `check_rls` 0 ·
`audit_stored_destinations` 0 · pytest 20 passed · ruff clean.

Carry-forward above is amended: the cache now has behavioural coverage and the
single-flight claim is proven rather than asserted. What remains is CI wiring
for four controls — `check_cache_behaviour`, `check_cache_scoping`,
`check_log_pii`, `check_tenant_coverage` (the last still exits 1).
