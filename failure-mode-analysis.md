# LinkOps — Failure Mode Analysis

Default stance: **fail-closed** (Module 05 DECIDE) — refuse rather than guess
where the missing dependency governs correctness or authorization; **fail-open
is the deliberate, bounded exception** for the cache and analytics counter,
where a degraded answer is safe and failing the product is not.

## Chunk 1 — Dependency inventory

| Dependency | Connection method | Configured timeout | Retry behavior |
|---|---|---|---|
| **Postgres** (app role) | TCP via SQLAlchemy pool (psycopg 3) | **connect 5s, pool checkout 5s** (added this cycle) + per-tx `statement_timeout` where it matters | none in-request (fail fast → 503); worker retries |
| **Postgres** (migration role) | TCP, worker's own pool | connect 5s, pool 5s, size 3 no overflow | worker: bounded exponential retry, then dead-letter |
| **Redis** (cache + queue) | TCP, redis-py | fails soft; blocking ops have the 5s brpoplpush timeout | cache: none (miss==outage); worker: reconnect w/ capped backoff |
| **DNS** | OS resolver (hostnames in `DATABASE_URL`/`REDIS_URL`) | inherits the 5s connect timeout — a resolve that hangs fails with the connection | none explicit; surfaces as a connect failure |
| **File system** | stdout only (logs to the orchestrator) | N/A | none — the process owns no log file, so no rotation/disk-full path |
| **Runtime** (memory/CPU/GC) | in-process | N/A | OOM → container restart; liveness `/health` is dependency-free so a restart is the correct action |
| **External APIs** | — | N/A | **none exist** — LinkOps calls no third party; the whole `.env` has no third-party base URL |

**Timeout audit:** the only entries that were `?` (library default = wait
forever) were the Postgres pools, closed this cycle. DNS is not separately
configured but is bounded because it rides the same 5s connect timeout — a
hanging resolve fails as a connect failure rather than hanging forever, which is
the property that matters.

## Chunk 2 — Failure mode table

| Dependency | Failure mode | Probability | User impact | Current handling | Desired handling |
|---|---|---|---|---|---|
| Postgres | **Down** (refused) | Low | redirect serves from cache while warm; management writes 503; `/ready` false → out of rotation | fail-closed: 503 envelope, no guessing; `/ready` pulls the instance | ✅ as-is |
| Postgres | **Slow** (wedged, responding at 8s) | Med | *the dangerous one* — without a timeout, pooled connections pile up and the API stops serving everything | **connect+pool timeout 5s** converts slow→fast-fail 503 | ✅ closed this cycle |
| Postgres | **Connection pool exhausted** | Med | requests wait, then 503 | `pool_timeout=5` bounds the wait; worker has its own isolated pool (bulkhead) | ✅ as-is |
| Redis | **Down** | Med | redirect still 302 (serves from DB); analytics clicks dropped w/ warning | fail-open (cache miss==outage); `/ready` ignores Redis | ✅ as-is |
| Redis | **Slow** | Low | a blocking worker op waits ≤5s then loops; redirect cache calls are non-blocking gets | bounded by the brpoplpush timeout | ✅ as-is |
| Redis | **Returns stale/unreadable value** | Low | a malformed cache entry could serve a wrong redirect | entry that fails to parse is discarded + invalidated, treated as a miss | ✅ as-is |
| DNS | **Resolution fails** | Low | cannot reach Postgres/Redis by hostname; looks like "connection refused" | rides the 5s connect timeout → fast fail, not a hang | ⚠️ acceptable; document that cryptic connect errors may be DNS |
| Worker | **Down / crashed** | Med | analytics counts stall; redirects unaffected | jobs park on the durable processing list; orphan-recovery on restart | ✅ as-is |
| Worker | **Poison job** (never succeeds) | Med | one job would loop forever without a bound | bounded retries (3) → dead-letter; exponential backoff | ✅ closed (Debugging M06) |
| Postgres | **Partial: read-only / writes rejected** (replica failover, read-only primary) | Low-Med | reads (redirect, list, search) fine; POST/PATCH 500 | writes hit `database_error` → opaque 500; `/ready` (a read) stays green so **monitoring lies** | ⚠️ **gap found by BREAK** — add a write-capability signal for write-serving instances |
| Runtime | **OOM** | Low | pod killed | liveness `/health` dependency-free → restart is the right action, not a false-positive on a DB blip | ✅ as-is |

## The one lesson this table encodes

**"Slow" is more dangerous than "down."** Down refuses immediately and the error
path fires; slow looks like working while it drains threads — the payment/fraud
cascade, and the retry-storm cascade LinkOps already fixed. Every row whose
handling is a *timeout* exists to convert a slow failure back into a fast one, so
the survivable failure (down) is the one the system actually experiences. The
timeout column having no `?` left in it is the whole point of this document.

## Where fail-open is deliberate (not accidental)

Only two places, both bounded and documented: the **redirect cache** (a cache
with a DB fallback) and the **analytics enqueue** (a lost count is a rounding
error). Everywhere else — authorization, tenant scoping, writes — is fail-closed,
because in a multi-tenant system guessing at the missing answer is how a
cross-tenant leak happens.
