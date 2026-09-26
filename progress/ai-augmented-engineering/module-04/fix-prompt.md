# Fix prompt — and a critique of the model prompt

The IDOR the step names is already closed (`require_role(..., "admin")` is the
first line of `create_invitation`, with three tests and a live 404 transcript).
So this prompt targets the vulnerability that **survives**: `teams`,
`team_memberships` and `invitations` are tenant-scoped and carry no row-level
security, so tenancy and team membership are two authorization systems that can
disagree with nothing reconciling them.

---

## First: two defects in the step's own "good prompt"

It is specific, well-structured, and would reliably produce two problems.

1. **It specifies `403`.** A 403 confirms the team exists, turning the endpoint
   into a team-enumeration oracle for anyone with an account. The Module 02 task
   tree says 404 for exactly this reason. *A precise prompt that specifies the
   wrong behaviour produces the wrong behaviour very reliably* — precision
   amplifies whatever you put into it, including a mistake.
2. **It says `team_members`; the committed vocabulary is `team_memberships`,**
   and `role = 'admin' (or role = 'owner')` leaves a parenthetical alternative
   for the agent to resolve. Both are the Module 02 finding — an agent given a
   description instead of the artifact guesses, and a plausible guess is the
   expensive kind. The prompt should paste the schema, not name it.

---

## The prompt

> In `api/alembic/versions/`, add migration `0010` enabling row-level security
> on three tables that currently have none: `teams`, `team_memberships` and
> `invitations`.
>
> **Schema, verbatim — do not infer it:**
> ```
> teams(id uuid pk, tenant_id uuid not null, name varchar(120), created_at timestamptz)
> team_memberships(team_id uuid, user_id uuid, role varchar(16), added_at timestamptz,
>                  PRIMARY KEY (team_id, user_id),
>                  FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE)
> invitations(id uuid pk, team_id uuid not null REFERENCES teams(id), email varchar(320),
>             role varchar(16), token_hash varchar(64), invited_by uuid,
>             created_at, expires_at, revoked_at, revoked_by, accepted_at, accepted_by)
> ```
>
> **For `teams`,** create a policy named `tenant_isolation` using **exactly** the
> predicate already used by `links` — copy it, do not rewrite it:
> `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`
> It must appear in **both** `USING` and `WITH CHECK`. `USING` alone filters reads
> and permits a write that lands in another tenant. Then
> `ALTER TABLE teams FORCE ROW LEVEL SECURITY` — without `FORCE`, the table owner
> bypasses the policy, and migrations run as the owner.
>
> **For `team_memberships` and `invitations`,** neither has a `tenant_id` column;
> both are tenant-scoped transitively through `team_id`. Use a policy of the form
> `team_id IN (SELECT id FROM teams)` — which resolves correctly because `teams`
> is itself policy-protected, so the subquery is already tenant-filtered. Do
> **not** add a denormalised `tenant_id` column to either table: two sources of
> truth for tenancy is the failure this fix exists to prevent.
>
> **Also required:** `USING` and `WITH CHECK` on all three, `FORCE` on all three,
> and a `downgrade()` that drops the policies and disables RLS.
>
> **Do not change** `app/services/teams_service.py`, `app/routers/teams.py`, any
> existing migration, or `app/rls.py`. Do not add a `tenant_id` column anywhere.
> Do not add tests — I am writing the verification separately, because a test
> written by the same pass that wrote the fix asserts the fix's own assumptions.
>
> **Definition of done, and it must be demonstrated failing first:** running
> `scripts/check_tenant_coverage.py` before this migration exits **1** and names
> all three tables; after it, exits **0**.

## Why the last paragraph is the load-bearing one

Everything above it describes a change. That paragraph is the only part that can
tell the difference between a fix and something shaped like a fix — and it works
because the check is external to the change and I watched it fail first.
