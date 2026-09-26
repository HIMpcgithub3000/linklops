# Module 07 — Background Jobs & Analytics

System Design Fundamentals, practitioner pack.

## Decisions

- `decisions.module_07.jobs_strategy = async_queue`
- `decisions.module_07.retention_days = 30` *(assumption — 30 is the module's own
  figure; a real retention period is a compliance call, not an engineering one)*

Async follows the Module 03 decision that kept the analytics write off the
redirect path, and the ~1000:1 read ratio: a synchronous insert makes database
latency into redirect latency and a database blip into a redirect outage.

## The ordering that is the whole module

The existing worker used `BRPOP`, which is **at-most-once**: the job leaves the
queue before it is applied, so a crash in between loses a click. No retries,
because a retry on top of a non-idempotent `count = count + 1` double-counts —
the incident the module describes. So the bias was toward undercounting.

Three changes, and doing them in this order is the point:

1. **the write became idempotent** — one statement (below)
2. **delivery became at-least-once** — `BRPOPLPUSH` onto a processing list
3. **failures became bounded** — retry with backoff, then dead-letter

Doing (2) before (1) converts every lost click into a double-counted one, which
is strictly worse: an undercount is a known unknown, an overcount is the number
the business acts on.

## Idempotency: a property of the statement, not a promise of the caller

```sql
WITH stored AS (
    INSERT INTO click_events (id, link_id, tenant_id, clicked_at, ...)
    SELECT :event_id, l.id, l.tenant_id, :clicked_at, ...
    FROM links l WHERE l.id = :link_id
    ON CONFLICT (id, clicked_at) DO NOTHING
    RETURNING link_id, clicked_at
)
INSERT INTO analytics (...) SELECT ... FROM stored
ON CONFLICT (link_id, timestamp_bucket) DO UPDATE SET count = analytics.count + 1
```

The rollup increment is fed by the rows the raw insert *actually created*. A
replay conflicts on the click_events primary key, `stored` is empty, and `count`
does not move. Two statements would lose exactly this: the insert would be
idempotent and the increment would not.

Three supporting choices, each of which is wrong the obvious way:

- **`event_id` is minted, not derived.** A key of `(link_id, ip, minute)` would
  collapse two genuine clicks by the same visitor into one — trading a visible
  double-count for an invisible undercount.
- **`clicked_at` is minted at enqueue time, not stamped by the worker.**
  `click_events` is RANGE-partitioned on `clicked_at` with PK `(id, clicked_at)`,
  so a worker-stamped timestamp sends a retry into a *different partition* and
  the ON CONFLICT never fires. It also means a job delayed by a backlog is
  counted in the hour it happened rather than the hour it was processed.
- **`tenant_id` comes from `links`, never from the payload.** The payload
  crossed a queue and is therefore data; a worker that takes a tenant id from
  data is one malformed job away from writing into the wrong tenant.

## Migration 0010 — the rollup was outside the tenant boundary

Found while wiring the analytics endpoint: `analytics` was readable by the app
role with **no RLS at all**, and `check_tenant_coverage.py` did not flag it —
that control finds tables scoped through a foreign key, and `analytics` had
**no FK to links**, so it was classified as not tenant-scoped. A silent false
negative on the one table this module exposes.

Two changes, and the first is what makes the second detectable rather than
merely correct:

- `fk_analytics_link` → `links(id) ON DELETE CASCADE`. It was missing entirely,
  so the rollup carried link ids nothing guaranteed existed.
- `tenant_isolation` policy, ENABLE + FORCE, scoped **through links** rather
  than by denormalising `tenant_id` onto the rollup. A denormalised copy would
  be the faster predicate and a second place the truth is written — and a rollup
  row whose tenant disagrees with its link's is a cross-tenant read that looks
  perfectly consistent from inside the table.

`check_tenant_coverage.py` after: tenant-scoped tables **5 → 6**, with
`analytics` in `protected`.

## Built

| file | what |
|---|---|
| `app/services/clicks.py` | payload construction, bounded UA/referrer, salted IP hash |
| `app/routers/redirect.py` | enqueues the full event instead of a bare link id |
| `app/redis_client.py` | processing + dead-letter list names |
| `worker/main.py` | reliable queue, retries, dead-letter, orphan recovery, idempotent apply |
| `app/routers/links.py` | `GET /links/{id}/analytics?from=&to=` |
| `app/schemas/link.py` | `LinkAnalytics` (wire name `from`, half-open window echoed back) |
| `app/config.py` | `CLICK_RETENTION_DAYS`, `CLICK_IP_SALT` + production guard |
| `scripts/purge_click_events.py` | retention purge by partition drop |

**The endpoint reads the rollup, not raw events** — a retention decision, not a
performance one. Raw events are purged after 30 days; counting them would make
the endpoint's answer shrink every time the purge ran, and a figure that changes
when nothing happened is worse than no figure.

**The purge drops partitions rather than deleting rows.** A month entirely older
than the cutoff goes with `DROP TABLE` — constant time, no dead tuples, no
vacuum debt on a table fed by the highest-traffic path in the product. Only the
partially-expired month and DEFAULT need a bounded `DELETE`.

**`CLICK_IP_SALT` is fatal outside development.** An unsalted digest of an IPv4
address is a reversible encoding, not anonymisation: the space is 2^32 and a
reverse table is minutes of work. Environment-gated like the CORS list, so there
is no separate flag to forget.

## Two defects found by running it

**1. The worker died on a transient broker read.** `redis-py` 8.1 surfaces the
blocking timeout on `BRPOPLPUSH`/`BLMOVE` as a socket `TimeoutError` rather than
returning `None` — `BRPOP` returns `None` from the same situation, so moving to
a reliable queue silently turned "queue is empty" into an exception that killed
the process. Reproduced directly: `blmove(timeout=5)` raises at 5.0s while
`brpop(timeout=3)` returns `None` at 3.1s. A dead worker is a queue that grows
with nothing user-facing reporting it — the redirect keeps serving.

Fixed by separating the two cases, which are only distinguishable by type:
`TimeoutError` → an idle poll, `continue`; `RedisError` → reconnect with capped
exponential backoff and re-run orphan recovery. Treating the first as an outage
means an idle worker logs an error every five seconds, which trains everyone to
ignore the line that will matter.

**2. Old-format jobs were left on the queue.** 72 bare-uuid payloads from the
previous format were still queued when the new worker started, and were
dead-lettered as malformed rather than crashing the worker or being silently
dropped. That is the deploy-compatibility case, handled — and the reason a
poison message goes to a list instead of `/dev/null`: it is a bug report with
the payload attached.

## Evidence (live, API :8099, worker, `linkops-postgres` :5433, `linkops-redis` :6379)

```
1. two redirects (UA EvidenceAgent/1.0, Referer https://news.example/post)
   click_events: 2 rows, user_agent + referrer stored, ip_hash 64 hex chars

2. GET /links/{id}/analytics?from=2000-01-01&to=2100-01-01   with X-API-Key
   200 {"clicks":2,"last_clicked_at":"..."}          without key -> 401

3. idempotency: same payload replayed twice
   click_events 2 -> 2, analytics count 2 -> 2, dead-letter unchanged

4. privacy: 0 IP-shaped values stored anywhere in click_events
   stored hash      6243193f5479...
   unsalted sha256  12ca17b49af2...   <- different, so the salt is applied

5. retry + dead letter: a job with an uncastable clicked_at
   retry 1/3, retry 2/3, then dead-lettered after 3 attempts

6. queue-down drill: docker stop linkops-redis, API and worker left running
   redirect 1  302  6.4ms      worker: ConnectionError, reconnecting in 2.0s
   redirect 2  302  4.0ms      worker: ConnectionError, reconnecting in 4.0s
   redirect 3  302  4.4ms      worker survived (pid count 1)
   redis restarted -> worker recovered, next click stored (2 -> 3)

7. retention purge (30d, cutoff 2026-07-18)
   before  2026_06: 1   2026_08: 2   default: 1     analytics 3 rows / 5 clicks
   purge   dropped partition click_events_2026_06, deleted 1 row from default
   after   2026_08: 2                              analytics 3 rows / 5 clicks
   second run: dropped (none), deleted 0            <- idempotent
```

## State

`check_cache_behaviour` 0 · `check_cache_scoping` 0 · `check_log_pii` 0 ·
`check_log_injection` 0 · `check_url_policy` 0 · `check_rls` 0 ·
`audit_stored_destinations` 0 · `check_tenant_coverage` **1** (pre-existing:
`teams`, `team_memberships`, `invitations`) · pytest 20 passed · ruff clean.

## Carry-forward

- **`check_rls.py` does not cover `analytics`.** `app/rls.py` compares against
  one canonical predicate, and the analytics policy is an EXISTS subquery
  through `links`, so adding it to `TABLES` would fail on shape rather than on
  correctness. `check_tenant_coverage.py` sees the policy exists; nothing yet
  verifies it actually blocks a cross-tenant read.
- **Orphan recovery assumes a single worker.** `recover_orphans()` reclaims the
  whole processing list, which with several workers would claw back jobs another
  worker is mid-way through. Needs a per-worker processing list before scaling
  out. Written down in the source too.
- 73 entries on `analytics:dead`, 72 of them old-format payloads from before
  this module. They need draining or deliberate discarding.
- Clicks that arrive while Redis is down are still lost — accepted, and the
  reason the redirect never waits on the queue.

## BREAK / FIX — the missing retry bound

**Injected.** The `attempts >= MAX_ATTEMPTS` branch that dead-letters was
removed, so a failing job is always requeued. The diff justified it as not
wanting to lose a click to a transient fault — true as far as it goes, and it
quietly removes the only thing that distinguishes "retry" from "forever".

**Symptom.** Nothing errors. The redirect keeps returning 302 and the API keeps
returning 200. The only evidence is in the queue:

```
poison job attempt count    16 and climbing, no ceiling
dead-letter list            73 -> 73     (never grows)
queue depth                 never drops below 1
a REAL click behind it      landed after 10.1s, not near-instant
```

The last line is the part that makes this more than a leak. Backoff is
`0.5s x attempts`, so the worker degrades as the poison job ages — by attempt 16
it sleeps 8 seconds per cycle. An unbounded retry does not just fail to give up;
it progressively converts the worker into a process that mostly sleeps on work
that can never succeed.

**Fixed** by restoring the bound. After: `retry 1/3`, `retry 2/3`,
dead-lettered after 3 attempts, queue drains to 0.

## Third standing control

`api/scripts/check_analytics_pipeline.py` — eighth in the family. Five claims,
every one of which failed silently in some form while this module was built:

| claim | asserted by |
|---|---|
| queue agreement | no module hardcodes the queue name instead of importing the constant |
| payload shape | required fields present, no raw IP on the queue, hash differs from an unsalted digest, `clicked_at` carries an offset |
| idempotency | three identical jobs → 1 click event and rollup 1 |
| distinctness | a genuinely different event still counts → 2 and 2 |
| tenant isolation | another tenant reads 0 from the rollup through the 0010 policy |
| retry bound | final-attempt job is dead-lettered; a job with attempts left is requeued with an incremented count |

It imports the real `worker/main.py` by path rather than reimplementing the
statement — a control that copies the logic it checks proves only that two
copies agree.

Two design notes worth keeping:
- **Three identical applications, not two.** Two would pass for an
  implementation that happens to alternate.
- **Its own queue keys.** The first version pushed onto the real queue and
  failed intermittently, because the *running* worker consumed the requeued job
  before the assertion could read it back. Controls must not share a queue with
  the thing they are testing.

**Watched failing:**

| reintroduced | exit | reported |
|---|---|---|
| retry bound removed | 1 | `a job on its final attempt was not dead-lettered -- it will be retried forever` |
| rollup increment not gated on the raw insert | 1 | `three identical jobs incremented the rollup to 3, not 1` + `a genuinely distinct click was swallowed: events=2, rollup=4` |

## Final state

`check_analytics_pipeline` 0 · `check_cache_behaviour` 0 · `check_cache_scoping`
0 · `check_log_pii` 0 · `check_log_injection` 0 · `check_url_policy` 0 ·
`check_rls` 0 · `audit_stored_destinations` 0 · `check_tenant_coverage` **1**
(pre-existing) · pytest 20 passed · ruff clean.

End to end after the fix: redirect 302, analytics endpoint returns
`{"clicks":7,...}` for the evidence link.

The `check_rls` carry-forward above is now partly retired — nothing verifies the
analytics *predicate text*, but `check_analytics_pipeline` proves the policy
actually refuses another tenant's read, which is the property that matters.
