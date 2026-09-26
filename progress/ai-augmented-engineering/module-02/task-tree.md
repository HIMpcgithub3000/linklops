# Team Collaboration — task tree

Medium granularity, 10 tasks, none over 150 lines. Top-down, with the decision
layer settled before task 1 because the hard constraints are cross-cutting and
cannot be discovered task by task.

## Decision layer — settled before anything runs

These are not tasks. They are the three answers that determine what the tasks can
mean, and handing them to an agent already answered is the difference between
delegating and hoping.

| Decision | Answer | Why it is load-bearing |
|---|---|---|
| Is an invitation single-use, and bound to what? | Single-use, bound to an email address AND a token. Token is the credential; email is the constraint on who may redeem it | Determines whether acceptance is idempotent, whether revocation targets one row or many, and whether a forwarded link is a vulnerability |
| Expiry: read-time or stored status? | Evaluated at read time against `now()`. No job flips a status column | A job leaves a window where an expired invite still resolves. Same rule already applied to link expiry in the LinkOps service |
| Revocation: delete or state transition? | State transition to `revoked`, row retained | A deleted row cannot answer "who revoked this and when", and the audit constraint requires that it can |

Fourth, cross-cutting, and the reason top-down was chosen: **authority is a
property of now, not of when the token was minted.** Every authorisation check
happens at the moment of the action against current state — never against a
snapshot taken at send time. This decides three of the step's edge cases at once.

## Tasks

**T1 — Create teams and memberships schema + migration**
- *Context:* existing migration directory and naming convention; the role enum the product requires (owner/admin/member)
- *Output:* one migration creating `teams` and `team_memberships`, with a unique constraint on `(team_id, user_id)`
- *Accept:* migration applies and rolls back; inserting a duplicate membership fails with a constraint error, not a duplicate row
- *Depends on:* —

**T2 — Role enforcement primitive**
- *Context:* T1 schema; the constraint that enforcement must be *verifiable on every protected action*
- *Output:* one function/dependency that resolves the caller's role for a team and refuses by default
- *Accept:* a protected route with no role declared is **refused**, not allowed. This is the test that matters — default-deny is checkable from outside by calling an undeclared route
- *Depends on:* T1

**T3 — Invitations schema + migration**
- *Context:* T1; the three decisions above
- *Output:* migration creating `invitations` with token hash, invited email, team, inviter, `expires_at`, `revoked_at`, `accepted_at`
- *Accept:* an invite row can be expired, revoked and accepted independently; the token itself is not stored in plaintext
- *Depends on:* T1

**T4 — POST /teams/:id/invitations (send)**
- *Context:* T2, T3; the idempotency decision
- *Accept:* 201 with a token for an admin; **404 for a non-member** (not 403 — a 403 confirms the team exists); re-inviting the same email returns the *same* invitation, not a second one
- *Depends on:* T2, T3

**T5 — POST /invitations/:token/accept**
- *Context:* T3, T4; the authority-is-now rule
- *Accept:* accepting works once; the second attempt fails; an invite whose *inviter* has since lost admin still fails; expired and revoked both fail with an **identical** response to "no such token"
- *Depends on:* T4

**T6 — DELETE /teams/:id/invitations/:id (revoke)**
- *Accept:* revoked invite cannot be accepted; the row still exists with `revoked_at` and the revoking actor recorded
- *Depends on:* T4

**T7 — Audit log write path**
- *Context:* T2; the constraint that entries be complete enough for incident investigation
- *Output:* append-only writer plus entries for invite sent / accepted / revoked and role changes
- *Accept:* the sequence "invited → revoked → acceptance attempted" is fully reconstructable from the log alone, including who and when
- *Depends on:* T2, T4, T5, T6

**T8 — Comment threads with @mentions**
- *Accept:* a mention of a non-member does not leak that the user exists; comments are scoped to team membership at read time
- *Depends on:* T2

**T9 — Activity feed with ordered delivery**
- *Context:* the constraint that events remain consistent after reconnect
- *Accept:* a client reconnecting with a cursor receives every event exactly once, with a concurrent writer running — **the same test that caught offset-pagination drift in the LinkOps work**
- *Depends on:* T7

**T10 — Control: assert every protected route declares a role**
- *Output:* a script that enumerates routes and fails if any protected route has no role requirement
- *Accept:* passes clean; **and fails with a non-zero exit when a role declaration is deliberately removed** — a control nobody has watched fail is a control nobody knows is on
- *Depends on:* T2

## Critical path

T1 → T2 → T3 → T4 → T5 → T7 → T9 — seven of ten. T6, T8 and T10 hang off it and
can run in parallel once their parent lands.

The path is long because authorisation is genuinely sequential: nothing can be
protected before the role primitive exists, and nothing can be audited before the
actions being audited exist. Attempting to parallelise T2 would mean each endpoint
inventing its own role check, which is precisely the failure the "explicit and
verifiable on every protected action" constraint is written to prevent.

## Acceptance criteria that are deliberately negative

Six of the ten tasks accept on something *not* happening: a duplicate not created,
a 404 rather than a 403, an identical response for three different failures, a
route refused by default, a control exiting non-zero. That is intentional — the
positive path is what an AI produces reliably and what a demo shows. The negative
path is where it guesses, and where the guess looks like working code.
