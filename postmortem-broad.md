# Postmortem: Systemic patterns across the LinkOps hardening cycle

*Blameless, broad-scope. Covers the class of failures surfaced while hardening
the LinkOps URL shortener, not a single incident. Times are UTC.*

## Summary

Across a hardening cycle, ten defects were found and fixed in a multi-tenant URL
shortener. Individually they span security, correctness, and availability; none
recurred once fixed. Read together they are **three systemic gaps wearing ten
costumes**. This postmortem treats the three gaps as the incident, because
fixing ten bugs one at a time leaves the disease that produced them. No user data
was lost; the highest-severity defect (a cross-tenant read reachable via SQL
injection) had no confirmed exploitation, and "unknown prior exposure" is stated
honestly rather than assumed zero.

## Timeline (representative, UTC)

- 09:15 — Customer security review reports personal data (email addresses) in an
  exported log stream. *(class 3: invisible failure — the response was clean.)*
- 09:21 — A second, pre-existing instance of the same class is found by the
  control written for the first: a driver error logged with `exc_info` quotes an
  entire database row.
- 14:20 — A tag-filter search returns a whole tenant's rows for a crafted input;
  traced to string-built SQL. A boolean oracle reads another tenant's data one
  character at a time. *(class 1: an unenforced boundary.)*
- ~15:40 — A load test shows 20 concurrent requests on a cold cache key produce
  20 database reads instead of 1 (thundering herd). *(class 1 + class 3.)*
- 16:21 — A readiness probe change makes a survivable Redis outage read as a
  fleet-wide outage. *(class 1: readiness reflecting a dependency the request
  path does not need.)*
- Ongoing — Several defects could have merged green because CI ran a subset of
  the checks. *(class 2: no gate.)*

## Root cause

There is no single technical root cause, which is the finding. The defects
cluster into three system-level causes:

1. **Boundaries were believed, not enforced.** Every component was correct in
   isolation; the faults lived in the contracts between them — a cache hit that
   never reaches Postgres and so escapes row-level security; a log stream with a
   different audience than the database it copies from; a queue whose payload
   assumed idempotency the writer did not provide.
2. **The verification that existed was not a gate.** Controls and tests were
   written, but CI ran only four of nine controls and no test suite, so a
   regression in an unenforced path could reach production without a red build.
3. **The failures were invisible by construction.** Almost every defect returned
   a well-formed response — a 200, a 302, a clean count — while being wrong. PII
   sat in a log nobody diffed; a cross-tenant read looked like a normal result;
   an undercount looked like quiet traffic.

## Contributing factors

*System and process gaps only — no individual is named, because naming one would
not close any of these.*

- **No enforced invariant at component boundaries.** Tenant isolation, cache/DB
  agreement, and queue idempotency were properties the code was expected to
  maintain, with nothing that failed loudly when it stopped.
- **CI treated checks as optional rather than as a merge gate.** The suite could
  be green while untested paths regressed; a Redis-dependent control had no
  Redis service to run against.
- **Correctness had no observability.** Because a wrong result and a right result
  were byte-identical, the only detector was a human noticing later — a customer
  security review, a load test, a hand-run drill.
- **Test infrastructure could target the wrong database.** The suite pointed at a
  populated database with no guard, so a mis-set variable could mutate real data
  rather than fail.

## Impact

- **Duration:** the underlying gaps existed for the life of the affected code
  paths; exact windows are unknown for the ones found by review rather than
  alert, and are marked so.
- **Users affected:** no confirmed user-facing incident. The injection defect had
  cross-tenant read potential; exploitation is unconfirmed, exposure window
  unknown.
- **Data affected:** none lost or corrupted. The PII-in-logs defect exported
  contact data to a stream with weaker handling than the source; scope of that
  export is the one item with a genuine data-handling consequence.

## Resolution

Each defect was fixed at its contract, not its symptom — invalidation on the
commit boundary, a bound `:tag` parameter, a single-flight cache fill, a
readiness probe reduced to Postgres, `exc_info` replaced by structured diag
fields. The immediate issues are closed and each fix was watched failing before
being trusted.

## Remediation items

*Bounded to the three that each close a class. A fourth-through-fifteenth list
was deliberately not written — a wish list is not a plan.*

| # | Action | Owner | Deadline | Status |
|---|--------|-------|----------|--------|
| 1 | For every component boundary, a standing control that enforces its invariant and is watched failing before trust (RLS, cache scope, cache behaviour, log-PII, analytics idempotency, readiness contract — nine now exist) | Platform | rolling | In progress |
| 2 | CI runs the full suite as a merge gate: nine controls + pytest against real Postgres and Redis services | Platform | done | Closed |
| 3 | Make correctness observable: a `cache: hit\|miss` field on the hot path, plus alerts on connection-pool utilisation and dead-letter growth (the earliest detectable signals, ahead of any user symptom) | Platform / SRE | next cycle | Open |

## Lessons learned

- **What went well:** the watched-failing discipline — reintroducing each defect
  and confirming its control goes red — repeatedly caught *second* instances of a
  bug class the first fix did not, and caught regressions during the fix itself.
- **What went poorly:** verification existed but was not enforced, so its
  presence was mistaken for protection. A control that does not gate merges is
  documentation, not a control.
- **Where we got lucky:** the two highest-severity defects (cross-tenant read;
  PII export) were found by a customer review and a load test, not by an
  attacker or a regulator. That is luck, and remediation item 3 exists so the
  next one is found by an alert instead.
