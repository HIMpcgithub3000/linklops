# Postmortem: Database outage on March 15 (blameless rewrite)

*Rewrite of a blame-full draft. The person is removed throughout; the subject of
every finding is a system that can be changed without changing the humans. Times
are placeholders where the original gave none — marked `??:??` so the gap is
visible rather than hidden.*

## Summary

A schema migration dropped an index on the `users` table, causing login and
other user-table queries to time out. Login was degraded for **[N] users over
[H] hours** until the migration was rolled back. *(The original's "some users …
for a while" is un-reviewable; the bracketed figures are what a leadership review
would require.)*

## Timeline (UTC — reconstruct precise stamps from deploy and alert logs)

- `??:??` — Migration applied to production, dropping the `users` index.
- `??:??` — First user-reported login errors. **Detection was by user report,
  not by an alert** — itself a finding.
- `??:??` — Migration identified as the cause.
- `??:??` — Migration rolled back; queries recover.

## Root cause

The deployment pipeline applied a schema migration to production without first
requiring it to pass against a staging database with production-like data and
volume. The index drop was harmless in a small local dataset and catastrophic at
production scale, so any check that did not run against production-like data
could not have caught it — which is why "it was not tested locally" is a symptom,
not the cause.

## Contributing factors

*System subjects only.*

- **Migration tooling does not gate on a staging run.** A migration can reach
  production without having executed against production-like data.
- **Branch protection does not require review on migration PRs.** A schema change
  can merge with zero approvals.
- **The release process has no pre-weekend deploy freeze or change-window
  policy,** so a high-risk change can ship into the lowest-staffing window.
- **No alert covers query-timeout rate or index health,** so the first detector
  was a user rather than a monitor.

## Remediation items

| # | Action | Owner | Deadline | Status |
|---|--------|-------|----------|--------|
| 1 | CI gate: every migration must run `up` then `down` against a staging database seeded with production-scale data before it can be applied to prod | Platform | 2 weeks | Open |
| 2 | Branch protection: migration-touching PRs require ≥1 review from a database owner (applies to everyone, not one person) | Platform | 3 days | Open |
| 3 | Alert on `users`-table query p99 latency and on any `DROP INDEX` in an applied migration diff, paging before user impact | SRE | 2 weeks | Open |
| 4 | Codify a change-window policy (no schema deploys Fri 16:00 UTC → Mon 09:00 without an approved exception) as an enforced pipeline rule, not a guideline | Eng lead | 1 week | Open |

## Lessons learned

- **What went poorly:** the pipeline treated a schema migration like ordinary
  code, with no staging gate and no mandatory review — so a change that only
  fails at production scale had a clear path to production.
- **What would prevent the most recurrence:** a migration is not verified until
  it has run against production-scale data. That single gate closes the class,
  where "test more carefully" closes nothing.
- **Where we got lucky:** the failure was a query timeout and reversible by
  rollback. An index drop that had also been depended on by a later, already-shipped
  migration would not have rolled back cleanly.
