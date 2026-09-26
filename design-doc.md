# RFC: Rate limiting for the public API

Lightweight RFC — one page, and deliberately so. If this needs three pages I have
either not decided yet or I am defending rather than proposing.

## 1. Problem Statement

**One customer can currently degrade the API for every other customer, and our
only remedy requires a deploy.**

Three facts from last month, and the third is the one that changes the priority:

- 50,000 req/min from one key degraded response times for everyone. Not an
  attack — an accident, which means the next one is also inevitable.
- Mitigation was an on-call engineer editing a config file and redeploying at
  2 AM. Time-to-mitigate is bounded by a deploy, and the fix was manual, which
  means it is also unrepeatable and unauditable.
- Product wants a paid tier with **guaranteed** limits. A guarantee needs a
  mechanism to guarantee it. Today we cannot sell one because we cannot enforce
  one.

The business impact is not the degradation. It is that our blast radius is
"every customer" and our response time is "however long a deploy takes" — and we
are about to sell a promise we have no way to keep.

**Not in scope:** authentication, quota billing, DDoS absorption at the edge.

## 2. Proposed Approach

A fixed-window counter in Redis, checked per request, keyed by **what each limit
protects** rather than uniformly by client.

| surface | key | why this key |
|---|---|---|
| auth failures | key id | keying by IP lets a distributed attacker walk one credential |
| writes | tenant | the threat is one account's volume; an IP key punishes a customer behind NAT |
| public reads | client IP | there is no account — the caller is anonymous by design |
| authenticated reads | tenant | bulk scraping of one tenant's own data |

Three properties, each a decision rather than a default:

- **Fail open.** If Redis is unavailable the request proceeds. A rate limiter
  that takes the API down when *its own dependency* fails has converted an abuse
  control into an outage.
- **`Retry-After` on every 429.** Without it a well-behaved client retries
  immediately and makes things worse.
- **Limits are data, not code.** Stored per key, so raising a customer's limit is
  an UPDATE — which is what makes the paid tier a configuration change and
  removes the 2 AM deploy from the incident path.

Fixed window over sliding: one INCR and one EXPIRE. Its known flaw — up to 2×
the limit across a window boundary — does not matter for limits that are
order-of-magnitude judgements rather than precise budgets.

## 3. Alternatives Considered

- **Edge/CDN rate limiting.** Cheaper, no code, absorbs volumetric attacks we
  cannot. Rejected as the *only* mechanism because it cannot key on tenant — it
  sees IPs — so it cannot express "this customer's paid limit", which is the
  requirement driving the work. Worth adding *alongside*, later, for the
  volumetric case.
- **In-process counters.** No Redis, no network hop, lowest latency. Rejected
  because limits become per-instance: at N instances a customer gets N× their
  limit, and the number silently changes when we scale. A limit that depends on
  our deployment topology is not a limit we can sell.
- **Token bucket instead of fixed window.** Better burst behaviour and genuinely
  more correct. Rejected *for now* on complexity — it is the natural upgrade once
  we have data on real traffic shapes, and the storage key layout above does not
  change when we switch.

## 4. Risks and Mitigations

| risk | mitigation |
|---|---|
| Redis becomes a hard dependency of every request | Fail open; limiter never returns an error of its own |
| Added latency on the hot path | One INCR; measure before rollout and set a budget |
| Wrong limits break legitimate customers | Ship in **observe-only** mode first — count and log, reject nothing |
| Key derived from client-supplied data | Key on the socket peer, **not** `X-Forwarded-For`, until a trusted proxy is known to strip it. *A rate-limit key an attacker can choose is not a rate limit.* |

**The risk I am least sure about, named on purpose:** whether a fixed window is
acceptable to the customers we sell guarantees to. A client sending an even 100
req/min can legitimately be rejected if their traffic straddles a boundary, and
"you exceeded your limit" is a hard conversation when their own graph says they
did not. I do not know how bursty real integration traffic is. If it is bursty,
this design needs the token bucket sooner than "later".

## 5. Open Questions

1. What are the actual limits per tier? I have proposed a shape, not numbers, and
   the numbers need traffic data plus a product decision.
2. Do paid customers get a hard limit or a soft one that alerts us first?
   Rejecting a paying customer is a support cost; not rejecting them is the
   original problem.
3. Who can raise a limit, and is it audited? This is the 2 AM path — if the
   answer is "any engineer, no record", we have replaced a deploy with an
   untracked write.
4. Does the redirect-equivalent public path need a limit at all, given the real
   protection is credential entropy? I think yes, because it makes enumeration
   visible as a *rate* rather than as diffuse background 404s — but that is a
   detection argument, not a prevention one, and it should be argued as such.

---

## Appendix — review of Jamie Chen's Event Processing RFC (Module 03 BREAK/FIX)

Three gaps, ranked by what breaks if the team builds exactly what is written.

### 1. No Alternatives Considered

Straight from problem to a 3-broker Kafka cluster. Nothing on a Redis list, SQS,
or a Postgres-backed **outbox table** — which at 2,000 events/sec is comfortably
in range and adds *zero* new operational surface.

*Consequence:* seven weeks and permanent Kafka operations committed without the
cheaper option ever being argued. A reviewer who thinks it is over-engineered has
to reconstruct the comparison themselves, so most won't — and approval will look
like agreement when it is only absence of effort.

### 2. The capacity claim is arithmetic on the wrong numbers

> "Current peak is around 2,000 events/second, benchmarks show 10,000
> writes/second, so we have plenty of headroom."

Three separate errors:

- **2,000/sec is measured while processing is synchronous.** The request path *is*
  the throttle. This design removes it, so the arrival rate is no longer bounded
  by anything that was measured.
- **Steady state vs steady state.** The dangerous case is *recovery*: after any
  consumer outage the backlog drains at maximum consumer speed. The peak this
  design must survive is a burst nobody has estimated.
- **10,000 writes/sec ≠ 10,000 of this workload** — different row width, different
  indexes, no concurrent analytics reads.

*Consequence:* it works for weeks, then the first consumer restart replays a
backlog that takes the analytics database down. The outage is caused by the
recovery, not by the original fault.

### 3. A mitigation that does not mitigate its risk

> "If Kafka goes down, events will be lost. Mitigation: replication factor of 3."

Replication protects against a **broker** failing. It does nothing about the
**publish** failing — unreachable cluster, partition, full disk, expired cert.

The API publishes *during the request*, so there is an unmade decision at the
centre of this design: **when the publish fails, does the request fail or is the
event dropped?**

- Fail the request → Kafka is now a hard dependency of every API call, and
  availability is *worse* than the 150–300 ms this project set out to remove.
- Drop the event → silent data loss, on the exact path the project exists to
  protect.

*Consequence, and why this is the most dangerous of the three:* it reads as
already handled. A reviewer scanning the risks table sees the row closed.

**Fix:** an outbox — write the event to the same database transaction as the
request, and a relay publishes it. The publish can then fail without either
failing the request or losing the event, because the durable record is already
committed.

### Runners-up

- *"Retried 3 times, then a DLQ for manual inspection"* — manual by whom, alerted
  how, with what runbook? A DLQ nobody watches is a data-loss queue with extra steps.
- *"Single partition for order-sensitive events"* caps purchase throughput at one
  consumer permanently — a scalability cliff hidden inside a mitigation, in a doc
  whose premise is that traffic will grow.

### What I take back to my own RFC

Gap 3 is the one I am most at risk of writing myself: a mitigation that names a
real mechanism which addresses a *neighbouring* risk. My own latency row said
"measure before rollout and set a budget" without naming the budget — the same
hedge in number-shaped clothing. Now: **if the check adds more than 2 ms at p99 it
does not ship in the request path.**
