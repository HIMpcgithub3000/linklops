# Postmortem: OrderProcessor silent order loss

**Date of incident:** Wednesday, 14:00–16:02 (2h 02m)
**Severity:** SEV-1 — revenue-affecting, silent
**Status:** Resolved; all affected orders reprocessed
**Format:** Timeline-based, with a five-whys nested in the root cause section

---

## 1. Summary

For 94 minutes, OrderProcessor charged customers and did not create their orders.
A config change removed a field the running code still required; a broad
`try/except` swallowed the resulting error, logged it at DEBUG, and returned
**200 OK** to the client. Customers saw a success page. **1,400 orders,
approximately $186,000**, were affected. Every monitor was green for the entire
duration, because every monitor measured whether the service was *responding*,
not whether it was *working*. Detection came from customers on social media
38 minutes in; the first rollback attempt failed because the rollback automation
had not been exercised since an infrastructure migration four months earlier.
No orders were permanently lost — payment processor logs let the team reprocess
all 1,400 by 16:02 — but for 94 minutes the system's own records were wrong and
the system did not know it.

---

## 2. Timeline

| Time | Event | Elapsed |
|---|---|---|
| **14:00** | v2.14 deploys, removing the deprecated `warehouse_routing` config field. Deprecation policy was followed. Order writes stop immediately. | 0 |
| 14:00–14:22 | Dashboard green. Health checks pass. 200s returned. **Nothing in the system indicates a problem, because nothing in the system is looking.** | |
| **14:22** | First external signal: customers post about missing confirmation emails. Support tickets begin. | **+22 (detection by users)** |
| 14:23–14:38 | Flagged in `#cs-escalations`. Symptom is triaged as an email delivery delay — a known prior failure with the same customer-visible signature. No investigation opens. | +38 lost to ambiguity |
| **14:38** | Second ticket wave. Investigation begins. | **+38 (team aware)** |
| 14:42 | Monitoring checked: response codes 200, latency normal, CPU and memory normal. **The dashboard is not broken. It is answering a question that cannot detect this failure.** | |
| **14:55** | Order database queried directly: zero new rows since 14:00. The nature of the failure is now known. | **+55 (true state known)** |
| 15:02 | v2.14 identified as cause. Rollback attempted. | +62 |
| **15:08** | **Rollback fails** — the script references a deployment artifact path invalidated by an infrastructure migration four months ago. | +68 |
| 15:15 | Escalated to platform team for a manual rollback. | +75 |
| **15:34** | Manual rollback to v2.13 completes. Order processing resumes. | **+94 (mitigated)** |
| 15:45 | Manual reprocessing of dropped orders begins, sourced from payment processor logs. | |
| **16:02** | 1,400 orders reprocessed, confirmations sent. Resolved. | **+122** |

### What the timeline makes visible

- **Time to detect: 38 minutes, all of it supplied by customers.** The system
  contributed nothing to its own detection.
- **Time from detection to correct diagnosis: 17 minutes**, spent ruling out a
  monitoring stack that was reporting healthy and was correct to.
- **Time from diagnosis to mitigation: 32 minutes, of which 26 were spent on a
  recovery path that did not work.**
- Order loss ran at roughly **15 orders/minute**. With a zero-orders alert firing
  at 5 minutes and a working rollback taking 6, this is an **11-minute, ~165-order
  incident**. **About 88% of the damage was caused by detection and recovery, not
  by the bug.**

---

## 3. Root cause

The trigger was a config removal. The trigger was safe to make — the field had
been deprecated for six months and the policy was followed. What made it
dangerous is three properties of the system, and they are **parallel, not
sequential**: each was independently sufficient to let this happen, and fixing
any one of them alone would have contained it.

### Root cause 1 — the service could report success for work it did not do

Five whys:

1. Customers were charged but no orders were created. *Why?*
2. OrderProcessor returned 200 without writing to the database. *Why?*
3. The missing-field error was caught by a broad `try/except` around order
   creation. *Why did that return 200?*
4. Because the handler treated "I caught an exception" as "I handled it" — it
   logged at DEBUG and fell through to the success path. *Why is that possible?*
5. Because **the HTTP status is produced by the request handler completing, not
   by the order existing.** Nothing structurally couples the response code to the
   outcome it claims to report.

That last line is the root cause. A broad `except` is the local instance; the
general defect is that **success is asserted by the transport layer rather than
established by the work.** A 200 from this service was never evidence that an
order existed — before the incident, after the incident, or during it. The
incident only made that visible.

### Root cause 2 — monitoring measured the system, not the outcome

Response codes, latency, CPU, memory. Four signals, all green, all accurate.
They are **proxies for correctness that hold only while the failure modes are the
ones they were chosen for** — crashes, slowness, saturation. A failure that keeps
the process healthy and returns quickly is invisible to all four *by construction*.

There was no `orders_per_minute` signal and no alert on it reaching zero. The
one number that describes what the service exists to do was not measured, so the
observability stack could report perfect health about a service producing
nothing. This is not a gap in coverage; it is the wrong axis. **Adding a fifth
system-health metric would not have caught this. Any single business-outcome
metric would have.**

### Root cause 3 — staging could not have caught this

Staging never had `warehouse_routing`. The config schemas diverge. So the
pre-production environment was structurally incapable of detecting a defect in
config-schema compatibility — **it differed from production in precisely the
dimension being changed.** The change passed staging, and passing carried no
information. An environment that diverges from production in the dimension under
test is not a weaker test of that change; it is not a test of it at all.

### The blameless point, stated as a fact rather than a value

**Nobody deviated from any process in this incident.** The deprecation policy was
followed. The deploy went out through the normal pipeline. Staging passed.
Monitors were watched. The on-call responded to what the evidence showed. Every
individual action was correct and the outcome was a $186,000 silent failure —
which means there is no person whose behaviour, if changed, would have prevented
it, and every remaining lever is a system change.

One process failure deserves naming on its own: **the field was deprecated for
six months and the running production version still read it.** Deprecation with
no verification that consumers have stopped is a label, not a process.

---

## 4. Contributing factors

These did not cause the incident. They set how long it lasted and how much it cost.

**The recovery path had the same defect as the primary path: believed to work,
never watched working.** Rollback automation had not been exercised since the
infrastructure migration four months earlier, and it referenced a path that
migration invalidated. It failed at the only moment it was ever asked to run.
Untested rollback is not a slower rollback — under load it is a 26-minute delay
followed by an escalation to a different team. *An untested recovery path is not
a recovery path; it is a belief about one.*

**"No confirmation email" and "no order was created" are indistinguishable to the
customer, and the escalation path had no step that separated them.** A prior
email-delivery incident with the identical customer-visible signature made the
benign reading the reasonable one. 16 minutes were lost to an ambiguity that a
single query — count orders in the last five minutes — resolves. The escalation
process did not require that query before choosing an interpretation.

**The error was logged, at a level nobody reads.** DEBUG in production is
functionally equivalent to not logging: the information existed, was retained,
and reached no one. The system was describing its own failure for 55 minutes into
a stream nothing was watching.

**Detection was outsourced to customers by default, not by design.** Nobody chose
Twitter as the alerting channel; it was simply the only channel that noticed.

---

## 5. Action items

| # | Description | Owner (role) | Deadline | Definition of done |
|---|---|---|---|---|
| 1 | Add an `orders_per_minute` metric to the OrderProcessor dashboard. **Page** to `#ops-alerts-page` when it is below **5** for 5 consecutive minutes. Separately, **warn** to `#ops-alerts` when it is below **50% of the trailing 4-week median for the same weekday and hour** for 15 minutes. | Observability team lead | 3 business days | Order writes are artificially stopped in staging and a page arrives within 6 minutes; order writes are throttled to 40% and a warning arrives within 16; both alerts link to the runbook. Verified by the drill, not by the alert's existence. |
| 2 | Remove the broad `try/except` on the order creation path. A missing required config field must fail the request with 5xx and write nothing. | OrderProcessor service owner | 2 weeks | A test asserts that removing a required config field yields 5xx and zero rows written — **and the test is confirmed to fail when the `except` block is restored.** |
| 3 | Response code must be derived from persistence, not from handler completion. Order creation returns 2xx only after a committed write is confirmed. | OrderProcessor service owner | 30 days | An injected post-write DB failure produces a 5xx in an integration test; no code path can return 2xx without a commit. |
| 4 | Rollback automation is exercised on a schedule, not on demand. | Platform team lead | 30 days | A weekly automated job rolls a canary service back one version and fails if it does not complete in under 5 minutes; a dashboard shows last-successful-rollback age and alerts above 8 days. |
| 5 | Config schema parity between staging and production. | Platform team lead | 30 days | A CI check diffs the config key sets of both environments and fails the pipeline on divergence, against an explicit exemptions file that starts empty. |
| 6 | Any support ticket tagged `missing-confirmation` is **automatically annotated** by the support tool with the order count for the last 5 minutes and the current `orders_per_minute` value. The triage decision is made from data attached to the ticket, not from recall. | Support tooling owner | 10 business days | A test ticket tagged `missing-confirmation` carries both numbers within 60 seconds of creation; the runbook's first branch reads them off the ticket. |
| 7 | A deprecation cannot be closed until zero consumers remain. | Platform team lead | 45 days | Closing a deprecation requires an attached search showing no reader of the field in any production config or service version currently deployed; enforced by the deprecation tracker, not by convention. |

Items 1 and 4 are the highest-value pair: together they convert this incident's
shape from 94 minutes to roughly 11.

**Three of these were rewritten during verification, and the reasons are worth
recording because they are the same reason three times.**

*Item 1 originally alerted on `orders_per_minute == 0`.* Exactly zero is the
easiest threshold to defend and the wrong one to ship: it catches only total
failure. Had v2.14 broken one fulfillment path instead of all of them, orders
would have fallen roughly 90% and the alert would never have fired — a slower,
quieter, and more expensive version of this same incident. A threshold set at the
value the incident happened to have is fitted to the past. It now pages below 5
and warns on a drop against its own trailing baseline, and it names the channels,
because an alert with no destination is a metric.

*Item 6 was originally a runbook step: "triage begins with an order-count
check."* That is the be-more-careful trap in procedural clothing. It relies on a
person under pressure at 03:00 remembering to run a query before forming a
hypothesis — and the whole reason 16 minutes were lost is that a *reasonable*
hypothesis arrived first. Rewritten so the support tool attaches the number to
the ticket: the data now arrives before the hypothesis can, which is a system
change. It is also a stopgap that mostly stops mattering once item 1 ships,
and it is ordered accordingly.

*Item 2's definition of done originally read "a test asserts the failure mode."*
A test that has never been seen failing proves nothing about what it detects —
which is precisely the defect this incident is about, applied to the fix for it.
It now requires the test to be confirmed red when the `except` block is restored.

---

## 6. Lessons learned

**What surprised us.** That the dashboard was not broken. The instinct on seeing
four green signals during an active revenue outage is that monitoring failed —
it did not. It answered its questions correctly. We had simply never asked it
whether the service was doing its job, only whether it was alive and fast. *A
healthy system and a working system are different claims, and we had only ever
instrumented the first.*

**What worked well, and is worth protecting.** Once the database was queried
directly, the cause was identified in **7 minutes** — the diagnostic instinct was
sound the moment it was pointed at the right layer. More importantly, **recovery
was possible at all because a second system held the record**: the payment
processor's logs were an independent account of what should have existed, so all
1,400 orders were reprocessed rather than reconstructed or lost. That redundancy
was not designed as an incident control and it is the reason this was a delay
rather than permanent data loss. It should now be treated as a control and
protected as one.

**What we would do differently tomorrow, before any action item ships.** Two
things, both free:

1. Any report of missing confirmation emails starts with a direct count of orders
   in the last five minutes, *before* the email system is examined. This costs
   30 seconds and would have saved 33 minutes.
2. When a rollback is attempted, page the platform team **in parallel**, not
   after it fails. We now know the rollback path is unverified; until item 4
   lands, its success is a hypothesis, and hypotheses should not be tested
   serially during an outage.

**The finding that generalises past this incident.** Three separate mechanisms —
the `except` block, the monitoring stack, the staging environment — were each
believed to hold a property they did not hold. The error handler was believed to
handle errors. Monitoring was believed to detect failure. Staging was believed to
test changes. Each belief was reasonable, each had been true at some point, and
none had ever been *watched failing*. **A control that has never been observed
catching something is not a control; it is an assumption with a dashboard.** The
action items above are written so that every one of them has to be seen failing
before it is accepted as done — that is why item 2 requires the test to fail when
the `except` is restored, and item 1 requires a drill rather than a deployed rule.

---

## Name scan

Searched. **No individual names appear in this document**, including in the root
cause, contributing factors, and action items. Owners are roles.

Two sentences were rewritten during the scan:

- "The on-call engineer assumed it was an email delay" → *"The escalation path had
  no step that separated 'no confirmation email' from 'no order was created'."*
  The assumption was reasonable given a prior incident with an identical
  signature; what was missing was a process step, and only the second phrasing
  names something that can be fixed.
- "The engineer removed a field that was still in use" → *"A deprecation was
  closed without verifying that consumers had stopped reading the field."*
  The policy was followed exactly. The first phrasing describes compliance as if
  it were an error and points the fix at a person; the second points it at
  action item 7.
