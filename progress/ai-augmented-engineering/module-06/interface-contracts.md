# Parallel sprint — three agents, three contracts

`parallelization_plan = interface_first` · `merge_strategy = sequential_integration`

## Single-writer resources — allocated before anyone starts

The section no dependency graph draws. These are not anyone's *output*; they are
shared mutable resources that every feature appends one line to, so N agents
produce N conflicts in one file, guaranteed rather than probable.

| resource | owner | why |
|---|---|---|
| Alembic revision `0010` | **Agent 1** | The chain is a linked list with one head. Two agents writing `down_revision = "0009"` create two heads and `alembic upgrade` refuses to run. |
| Alembic revision `0011` | **Agent 3** | Allocated, not chosen by whoever runs `alembic revision` first. |
| *(no migration)* | Agent 2 | Pure functions and a service hook. |
| `app/main.py` router registry | **integrator (me)** | Every agent exports `router` from its own module and **none of them touch `main.py`**. I add three lines at merge. |
| `app/services/teams_service.py` | **nobody** | See Agent 3's contract — the audit log subscribes to published events rather than editing call sites. |

---

## Agent 1 — Comment threads

**CREATES:** `app/models/comment.py`, `app/routers/comments.py`,
`alembic/versions/0010_comments.py`, `tests/test_comments.py`

**READS (do not modify):** `app/db.py`, `app/auth.py`, `app/rls.py`,
`alembic/versions/0006_teams_memberships.py`, `PROJECT-STANDARDS.md`

**MODIFIES:** *nothing.* Exports `router`; the integrator registers it.

**Data shape** — the table, verbatim, and the wire shape, which are not the same
document and must not be assumed identical:
```sql
comments(id uuid pk, task_id uuid not null, team_id uuid not null REFERENCES teams(id),
         author_id uuid not null, parent_id uuid null REFERENCES comments(id),
         body text not null CHECK (length(body) BETWEEN 1 AND 10000),
         created_at timestamptz not null, edited_at timestamptz null)
```
```json
{"id":"uuid","parent_id":"uuid|null","author_id":"uuid","body":"string",
 "created_at":"2026-08-15T09:31:04Z","edited_at":null}
```
`parent_id` exists **now**, nullable, even though threading ships later. Adding a
nullable column later is a migration; getting the shape right now is free. *A
slice may defer a feature; it may not adopt a representation it intends to
replace.*

**Integration points:** on every successful create, call
`activity.publish_event(team_id, "comment_created", actor_id, comment_id=...)`.
That is the **only** outbound coupling, and it is a call into a published
function — not an import of another agent's module.

**MUST carry `team_id`** even though it is derivable from `task_id`. Not
denormalisation for speed: RLS policies are per-table, and a policy that has to
join to find the tenant is a policy that can be got wrong.

---

## Agent 2 — @mention parsing and notification

**CREATES:** `app/services/mentions.py`, `tests/test_mentions.py`

**READS:** `app/services/teams_service.py` *(for `normalise_email` only)*

**MODIFIES:** *nothing.* No migration, no table, no route.

**Signature — this is the whole contract:**
```python
def extract_mentions(body: str) -> list[str]:
    """Return normalised, de-duplicated handles, in first-appearance order."""
```

**Cases the contract fixes so the agent does not choose:** `@user` matches
`[A-Za-z0-9._-]{1,64}`; a mention inside a fenced code block or inline backticks
is **not** a mention; `email@example.com` is **not** a mention (`@` preceded by a
word character); `@@x` yields nothing; duplicates collapse; matching is
case-insensitive and output is lowercase.

*Every one of those is a case an agent would otherwise decide silently, and each
decision is invisible in a diff and visible in production.*

**Integration:** `extract_mentions` is pure. Whoever notifies calls it. Agent 2
does **not** hook itself into comments — that is Agent 1's call site and Agent 1
does not exist yet from Agent 2's point of view.

---

## Agent 3 — Audit log

**CREATES:** `app/services/audit.py`, `alembic/versions/0011_audit_events.py`,
`tests/test_audit.py`

**READS:** `app/routers/activity.py` *(for the `publish_event` payload shape)*

**MODIFIES:** *nothing* — and this is the load-bearing line of the whole sprint.

The obvious audit design is middleware wrapping every endpoint, or an edit at
every mutation site in `teams_service.py`. Both make Agent 3 a writer to files
Agent 1 and the existing code own, which is exactly the shared-`Task`-model
collision from the module opener. Instead Agent 3 **subscribes**: it registers a
sink with the activity hub and writes what it receives.

```sql
audit_events(id uuid pk, team_id uuid not null, actor_id uuid not null,
             kind text not null, payload jsonb not null,
             at timestamptz not null)   -- append-only; no UPDATE, ever
```

**Consequence, stated rather than discovered:** the audit log records what was
*published*, not what *happened*. An action nobody publishes is invisible to it.
That is a real limitation of the decoupling and it is the price of Agent 3
modifying nothing — worth paying here, and it must be written down, because "we
have an audit log" and "we have a complete audit log" are different claims.

---

## Why these three are genuinely parallel

Not "different features" — **disjoint write sets.** Agent 1 writes `comments`,
Agent 2 writes nothing, Agent 3 writes `audit_events`. No two agents write the
same table, the same file, or the same migration id. The only shared thing any of
them touches is `publish_event`, and they touch it from opposite ends: Agent 1
calls it, Agent 3 receives from it, neither imports the other.
