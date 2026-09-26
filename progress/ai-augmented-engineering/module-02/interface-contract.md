# Interface contract — produced by T1, consumed by T2, T4, T5, T6, T7

Generated from the migration that actually ran, not from the task description
that requested it. That distinction is the whole fix: T2 failed because it was
given a dependency arrow (`depends on: T1`) instead of T1's output.

## Committed vocabulary

```
table:  team_memberships          NOT team_members
table:  teams                     columns: id, tenant_id, name, created_at
                                  NO owner_id — ownership lives in the membership row
roles:  owner | admin | member    NOT viewer  (enforced by ck_team_memberships_role)
key:    PRIMARY KEY (team_id, user_id)   — a membership has no surrogate id
fk:     team_memberships.team_id -> teams.id ON DELETE CASCADE
```

Verified against the live database, not transcribed from the migration file:

```
tables:        ['team_memberships', 'teams']
teams cols:    ['id', 'tenant_id', 'created_at', 'name']
allowed roles: CHECK role IN ('owner','admin','member')
```

## Where the fix belongs

**Task 1**, not Task 2. Membership is not a separate concern from teams here —
ownership is *expressed as* a membership row with `role='owner'`, so a team
without the membership table cannot represent its own owner. T1 was correct to
create both; what T1 failed to do was **publish** what it created.

So the revision is not to T1's implementation. It is to T1's definition of done:
a schema task is not complete until it has emitted the contract above.

## The three failures this prevents, and when each would have surfaced

| Collision | Fails at | Cost |
|---|---|---|
| `team_members` (wrong table name) | first query — `UndefinedTable` | cheap, loud |
| `teams.owner_id` (wrong location) | first query — `UndefinedColumn` | cheap, loud |
| `role='viewer'` (wrong vocabulary) | **first viewer invited** — `CheckViolation` | expensive, quiet |

The third is why a contract beats a longer prompt. `'viewer'` is a valid string.
The code compiles, the endpoint runs, review passes, and it breaks for a user
doing something reasonable. One decomposition gap produced one bug that stops the
build and one that waits.

## Rule

Every task that creates a schema, an interface or a vocabulary emits its contract
as an artifact. Every downstream task receives that artifact as context — not the
task description that produced it, and not a dependency arrow.

A dependency arrow says *when* to run something. It carries no information about
*what* was built, and an agent cannot infer a CHECK constraint it was never shown.

## Acceptance criterion added to T2

> Inserting a role outside the allowed set is refused.

That tests the vocabulary agreement directly rather than trusting two tasks to
have used the same words. It is a negative criterion, which is where the guessing
lives — the positive path was never in danger.
