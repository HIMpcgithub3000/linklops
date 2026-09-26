# Postmortem: OrderProcessor silent order loss — March 15

**Severity:** SEV-1 (revenue-affecting, silent) · **Duration:** 14:00–16:02 (2h 02m)
**Status:** Resolved; all affected orders recovered and reprocessed

*A rewrite of the March 15 postmortem. Same incident, same facts. The original
located the cause in individual decisions; this version locates it in system
properties, because the decisions in question were all correct and the outcome
still happened.*

---

## Summary

For 94 minutes, OrderProcessor accepted checkouts, charged the customer's card,
returned a success page, and did not create the order. **Approximately 1,400
orders, an estimated $186,000, were affected. Customers were charged for orders
that did not exist** — this was a financial exposure, not only a data loss.

The trigger was a routine deploy that removed a config field deprecated six
months earlier. The field was still read at runtime by the version in
production. The service did not crash: a broad `try/except` around order creation
caught the missing-field error, logged it at DEBUG, and returned **200 OK**.

Every monitoring signal stayed green for the full duration — correctly, because
every signal measured whether the service was responding, and none measured
whether it was working. Detection came from customers on social media at
**+22 minutes**; the team began investigating at **+38**; the true nature of the
failure was established at **+55**. The first rollback attempt failed and cost a
further 26 minutes.

No orders were permanently lost. The payment processor held an independent
record of every charge, and all 1,400 orders were reconstructed and reprocessed
by 16:02.

---

## Timeline

Roles only; every action below was taken by someone acting on the information
available to them at that moment.

| Time | Event | Elapsed |
|---|---|---|
| **14:00** | v2.14 deploys, removing the deprecated `warehouse_routing` config field, in accordance with the deprecation policy. Order writes stop immediately. | 0 |
| 14:00–14:22 | All monitors green. Health checks pass. 200s returned. No signal exists that could indicate the failure. | |
| **14:22** | Customers report missing confirmation emails on social media; support tickets begin. **First and only detection signal, and it is external.** | **+22** |
| 14:23–14:38 | Escalated to `#cs-escalations`. The symptom is triaged as an email-delivery delay — a prior incident produced an identical customer-visible signature, and no step in the escalation path distinguishes the two causes. | |
| **14:38** | Second ticket wave; investigation opens. | **+38** |
| 14:42 | Monitoring reviewed: response codes 200, latency, CPU, memory all normal. **All four readings were accurate. None of them can express this failure.** | |
| **14:55** | The order database is queried directly: zero rows written since 14:00. The failure mode is now understood. | **+55** |
| 15:02 | The v2.14 deploy is identified as the cause; rollback initiated. | +62 |
| **15:08** | **Rollback fails.** The automation references a deployment artifact path invalidated by an infrastructure migration four months earlier. It had not been exercised since. | +68 |
| 15:15 | Escalation to the platform team for a manual rollback. | +75 |
| **15:34** | Manual rollback to v2.13 completes; order processing resumes. | **+94 (mitigated)** |
| 15:45–16:02 | 1,400 orders reconstructed from payment processor logs, reprocessed, confirmations sent. | **+122 (resolved)** |

**What the intervals show.** Time to detect: 38 minutes, all of it contributed by
customers. Time from detection to correct diagnosis: 17 minutes, spent ruling out
a monitoring stack that was reporting healthy and was right to. Time from
diagnosis to mitigation: 32 minutes, **26 of them on a recovery path that did not
work.** Orders were lost at roughly 15/minute — so with an outcome alert at 5
minutes and a working rollback at 6, the same trigger produces an **11-minute,
~165-order incident. Roughly 88% of the cost came from detection and recovery,
not from the change.**

---

## Root causes

Three, and they are **parallel rather than sequential** — each was independently
sufficient, and fixing any one alone would have contained this.

### 1. The service could report success for work it had not done

A broad `try/except` around order creation converted a fatal configuration error
into a DEBUG log line and a 200 response. But the exception handler is the local
instance; the general property is that **the HTTP status was produced by the
request handler completing, not by the order existing.** Nothing coupled the
response code to the outcome it claimed to report. A 200 from this service was
never evidence that an order had been created — that was true before this deploy
and would have been true after it. The config removal did not create this
property; it revealed it.

*This is the answer to "why does removing a config field cause silent data loss
instead of a loud failure," and it is why 1,400 orders were affected rather than
one request failing loudly at 14:00.*

### 2. Monitoring measured the system, not the outcome

Response codes, latency, CPU, memory — four signals, all green, all accurate.
They are proxies for correctness that hold only for the failure modes they were
selected against: crashes, slowness, saturation. **A failure that keeps the
process healthy and returns quickly is invisible to all four by construction.**

No `orders_per_minute` signal existed and no alert on it. The one number
describing what the service exists to do was not measured, so the observability
stack reported perfect health about a service producing nothing.

The earlier postmortem described this dashboard as "misleading." It was not.
It answered its questions correctly; nobody had asked it the question that
mattered. That distinction decides the fix: **adding a fifth system-health metric
would not have caught this. Any single business-outcome metric would have.**

### 3. Staging was structurally incapable of catching this class of change

The staging environment has never had the `warehouse_routing` field — its config
schema and production's have diverged. So staging **differed from production in
precisely the dimension under change.** The deploy passed staging, and that pass
carried no information.

This matters beyond the fix, because it invalidates the obvious prescription.
"Test the change more thoroughly before deploying" was the earlier document's
central recommendation, and in this environment more thorough testing produces
the same green result more slowly. **The prescription was not weak; it was
unavailable.** No amount of diligence substitutes for an environment that can
represent the difference.

### On causation and people

**No process was deviated from.** The deprecation policy was followed. The deploy
went through the normal pipeline. Staging passed. Monitors were watched and
reported correctly. The initial triage matched a prior incident with an identical
customer-visible signature. Every individual action was reasonable given the
information available, and the result was a $186,000 silent failure.

That is not a defence of anyone; it is a finding. **It means there is no person
whose behaviour, changed, would have prevented this — so every remaining lever is
a system change.** If it were otherwise, at least one action item below would be
about a person, and none is.

One policy gap deserves naming on its own: **the field was deprecated for six
months and the production version still read it.** A deprecation that is not
verified against live consumers is a label, not a process — see AI-7.

---

## Contributing factors

These did not cause the incident. They determined how long it ran and what it cost.

**The recovery path had the same defect as the primary path — believed to work,
never watched working.** The rollback automation had not been exercised since an
infrastructure migration four months earlier, and it failed the first time it was
asked to run. An untested rollback is not a slower rollback; under load it is a
26-minute delay followed by an escalation to another team. *An untested recovery
path is not a recovery path. It is a belief about one.*

**"No confirmation email" and "no order was created" are indistinguishable to the
customer, and the escalation path contained no step that separated them.** A
prior email-delivery incident produced the same customer-visible signature, which
made the benign reading the reasonable one. Sixteen minutes were spent on an
ambiguity that one query resolves — and nothing in the process required that
query before an interpretation was chosen.

**The error was logged at a level nobody reads.** DEBUG in production is
functionally equivalent to not logging. The system described its own failure for
55 minutes into a stream nothing was watching.

**Detection was outsourced to customers by default rather than by design.** No
one selected social media as the alerting channel; it was the only channel that
noticed.

---

## Action items

Every item below is a change to a system, a pipeline, or a tool. None is a
change in anyone's intentions.

| # | Description | Owner (role) | Deadline | Definition of done |
|---|---|---|---|---|
| **AI-1** | Add an `orders_per_minute` metric to the OrderProcessor dashboard. **Page** `#ops-alerts-page` when it is below 5 for 5 consecutive minutes; **warn** `#ops-alerts` when it is below 50% of the trailing 4-week median for the same weekday and hour, for 15 minutes. | Observability team lead | 3 business days | Order writes are stopped in staging and a page arrives within 6 minutes; writes are throttled to 40% and a warning arrives within 16. Verified by drill, not by the alert existing. |
| **AI-2** | Remove the broad `try/except` on the order-creation path. An unhandled config or persistence error must return 5xx and write nothing. | OrderProcessor service owner | 2 weeks | A test asserts 5xx and zero rows on a missing required field — **and the test is confirmed to fail when the `except` block is restored.** |
| **AI-3** | Derive the response code from persistence rather than from handler completion: 2xx only after a confirmed committed write. | OrderProcessor service owner | 30 days | An injected post-write DB failure yields 5xx in an integration test; no code path can return 2xx without a commit. |
| **AI-4** | Add a backwards-compatibility check to CI that **fails the build** when a config field is removed while any currently deployed service version still references it at runtime. | Deployment platform lead | 3 weeks | The v2.14 change is replayed against the check and the build fails; a control change with no live readers passes. |
| **AI-5** | Exercise rollback automation on a schedule rather than on demand: a weekly job rolls a canary service back one version, plus a rollback dry-run stage in the deploy pipeline. | Platform team lead | 30 days | The weekly job fails the build if rollback does not complete in under 5 minutes; a dashboard shows last-successful-rollback age and alerts above 8 days. |
| **AI-6** | Enforce config-schema parity between staging and production. | Platform team lead | 30 days | A CI check diffs the config key sets of both environments and fails the pipeline on divergence, against an explicit exemptions file that starts empty. |
| **AI-7** | A deprecation cannot be closed while live consumers remain. | Deployment platform lead | 45 days | The deprecation tracker requires an attached scan showing zero readers of the field across all currently deployed service versions; enforced by the tool, not by convention. |
| **AI-8** | Support tooling auto-annotates any ticket tagged `missing-confirmation` with the last-5-minute order count and current `orders_per_minute`. | Support tooling owner | 10 business days | A test ticket carries both numbers within 60 seconds of creation; the triage runbook's first branch reads them off the ticket. |
| **AI-9** | Promote the payment-processor reconciliation used for recovery from an ad-hoc query to a maintained job. | Order platform lead | 45 days | A scheduled job diffs settled charges against created orders hourly and alerts on any non-zero gap older than 15 minutes. |

**AI-1 and AI-5 are the highest-value pair** — together they convert this
incident's shape from 94 minutes to roughly 11. **AI-4 prevents the trigger;
AI-2 and AI-3 make the trigger harmless** even if AI-4 is bypassed, which is why
both exist.

*Deliberately not included:* a review step requiring a second person to
double-check config changes. A reviewer reads the same diff with the same
information, and nothing in the diff indicates that production still reads the
removed field — so it taxes every future config change and would not have caught
this one. AI-4 is that control, automated and with access to the fact a human
reviewer lacks. **A control that cannot see the deciding information does not
become effective by being staffed.**

---

## Lessons learned

**What surprised us.** That the dashboard was not broken. Four green signals
during an active revenue incident feels like a monitoring failure, and it was
not — every reading was accurate. We had never asked the system whether it was
doing its job, only whether it was alive and fast. *A healthy system and a working
system are different claims, and only the first was instrumented.*

**What we learned about the trigger.** A deploy that follows every rule can still
be dangerous, because safety was never a property of the change — it was a
property of the system receiving it. Removing a deprecated field is a safe
operation in a system that fails loudly and an unbounded one in a system that
returns 200 for work it did not do. **The same action has a different blast
radius in a different system, so "was the change made correctly" is the wrong
question and "what would this system do with an incorrect change" is the right
one.**

**What worked, and now needs protecting.** Once the database was queried
directly, the cause was identified in **7 minutes** — the diagnostic instinct was
sound the moment it was pointed at the right layer. And recovery was possible at
all because a **second, independent system held the record**: the payment
processor's logs let all 1,400 orders be reconstructed rather than lost. That
redundancy was never designed as an incident control and is the entire reason
this was a delay rather than permanent data loss. AI-9 promotes it to one.

**What we would do differently tomorrow, before any action item ships.** Two
interim measures, both free, both expiring when the systems that replace them
land: any report of missing confirmation emails starts with a direct count of
orders in the last five minutes *before* the email system is examined (expires at
AI-1/AI-8); and a rollback attempt pages the platform team **in parallel** rather
than after it fails, since until AI-5 lands rollback success is a hypothesis and
hypotheses should not be tested serially during an outage (expires at AI-5).
*These are behaviour changes, which is legitimate for a two-week bridge and not
legitimate as an action item — which is why they are here and not in the table
above.*

**The finding that generalises past this incident.** Three mechanisms — the error
handler, the monitoring stack, the staging environment — were each believed to
hold a property none of them held. The handler was believed to handle errors.
Monitoring was believed to detect failure. Staging was believed to test changes.
Each belief was reasonable, each had been true at some point, and **none had ever
been observed failing.** *A control that has never been seen catching something is
not a control; it is an assumption with a dashboard.* Every definition of done
above requires the control to be watched working, which is why AI-2 requires the
test to go red and AI-1 requires a drill.

---

## What changed between the two versions

The facts are identical. Neither document has information the other lacks.

The first version named three people **eleven times** and produced five action
items, of which one changed a system — repairing the rollback script's path
without making it a tested path, so it rots again by the next migration. Its
root cause was a person and a verb. Its central prescription, test more
thoroughly, was **unavailable in this environment**, and the document could not
discover that because it stopped at the person and never reached the staging
schema.

This version names no one and produces nine action items, all system changes,
each with a role owner, a date, and a definition of done that requires the
control to be seen working.

**The test worth keeping:** replace every name in a postmortem with "someone."
If nothing actionable survives, the document was an attribution of fault wearing
a postmortem's structure. Run on the first version, nothing survives. Run on this
one, all nine items are unchanged — because none of them were about a person to
begin with.

And the second-order cost of the first version is the one it could never measure:
the deprecated-field cleanup that triggered this was exactly the maintenance work
organisations struggle to fund, and that document priced it at "your name in the
root cause." The next engineer leaves the dead fields in place. **A blame-heavy
postmortem does not just fail to prevent the next incident; it discourages the
work that prevents the ones after that.**
