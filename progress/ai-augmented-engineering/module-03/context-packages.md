# Context packages

## Part 1 — System-level context (reused by every task)

**Scope, stated first because it is the highest-value line in this document.**
This workspace holds two applications. `api/` and `worker/` are a Python 3.14 /
FastAPI service (1,446 files). `src/` and `tests/` are a JavaScript starter (4
files, no server, no persistence). *Team Collaboration work targets `api/`.* Every
prompt names its directory. In Module 01 an unscoped question produced a fluent,
entirely misattributed summary — every fact real, the subject wrong.

**Architecture.** FastAPI with `APIRouter` per plane, routers included in
`app/main.py`. SQLAlchemy 2.0 ORM with declarative models in `app/models.py`;
alembic for migrations, currently at `0006`. Middleware is an `@app.middleware("http")`
function; there is exactly one, for request logging. Sessions come from
dependencies in `app/db.py` — never constructed in a handler.

**Conventions.** `snake_case` for functions and variables, `PascalCase` for
classes, `snake_case.py` files. Routers live in `app/routers/`, logic in
`app/services/`, request/response shapes in `app/schemas/`. Routers stay thin.

**Constraints — the part that changes output rather than describing the project:**

1. **Isolation is structural.** Row-level security scopes every statement. Handlers
   must **not** filter by `tenant_id`. A redundant `WHERE` leaves the policy
   untested, so drift surfaces later with no control. *Any new table needs a policy.*
2. **Config is required, never defaulted.** A default is a silent substitution.
   Values are validated at import against `.env.example`.
3. **Errors never carry secrets or existence.** Unmapped DB errors return an opaque
   500; a resource belonging to another tenant returns 404, not 403.
4. **A control is not done until it has been watched failing** — exit 0 → 1 → 0.

## Part 2 — Task packages

### T2 — Role enforcement primitive

- **Contract from T1 (not a repository file):** table `team_memberships`; PK
  `(team_id, user_id)`; roles `owner|admin|member` — **not** `viewer`; `teams` has
  **no** `owner_id`, ownership is a membership row.
- **Pattern layer:** `app/db.py` (`get_session` — how a dependency establishes
  authority), `app/routers/links.py` (thin-router shape).
- **Constraints:** #1 and #3.
- **Accept:** a protected route with **no role declared is refused**, not allowed.
- **Excluded:** `worker/`, `infra/`, every migration before 0006.

### T3 — Invitations schema

- **Contract from T1:** as above, plus `teams.id` is the FK target.
- **Pattern layer:** `alembic/versions/0006_teams_memberships.py` (migration shape,
  CHECK-constraint style, GRANT line), `alembic/versions/0001_initial_schema.py`
  (**RLS policy syntax** — included specifically because T1 omitted RLS and passed).
- **Decisions, pre-answered:** single-use; token **hashed**, not stored plaintext;
  expiry evaluated at read time; revocation is a state transition.
- **Accept:** expired / revoked / accepted are independent; token column holds no
  plaintext; `check_rls.py` still exits 0 with the new table present.

### T4 — POST /teams/:id/invitations

- **Contract from T3:** exact column names, and whether the column stores the token
  or its hash — the next collision of Module 02's shape if left unstated.
- **Pattern layer:** `app/routers/links.py` (create handler, 201 shape),
  `app/schemas/link.py` (`extra="forbid"`), `app/main.py` (error handlers).
- **Constraints:** all four.
- **Accept:** 201 for an admin; **404 for a non-member**, not 403; re-inviting the
  same email returns the *same* invitation.

## What each package deliberately omits

No README, no CI config, no `Dockerfile`, no unrelated migrations, no `worker/`.
Not because they are unimportant — the Dockerfile matters a great deal — but
because they are irrelevant *to these tasks*, and relevance is the only axis that
matters here.

`.env.example` **is** included for any task touching configuration, against the
lesson's sort. In this project it is not documentation: it is the contract every
key is validated against at import, and code reading an undeclared setting refuses
to boot. The right classification depends on what a file *means* in the system,
not what kind of file it is.

## The hedge

Context selection is a bet. Module 02 proved the miss is invisible to review and
visible to a command — T1 passed all three criteria and still shipped tables with
no RLS, caught by querying `pg_class`. So each package above ends in an acceptance
check that runs regardless of whether I picked the right files.

## Addendum — the error-format rule (added after BREAK)

The BREAK exposed that this codebase has **two** error shapes, both deliberate,
with the rule for choosing between them existing only in my head. That is why no
context package could carry it: it was never text.

```
{"detail": "...", "problems": [{"field": ..., "reason": ...}]}
    → input the caller can fix. Names the failing field.
    → NEVER echoes the offending value (it is attacker-controlled by definition).

{"detail": "..."}
    → everything else, and specifically anything where naming the reason
      would tell the caller something they should not know.
    → the redirect path returns this identical body for unknown, expired,
      disabled and suspended-tenant. Distinguishing them is an oracle.
```

**Rule:** structured when the caller is expected to correct the request; flat when
the answer itself is information. The choice is about *disclosure*, not tidiness.

**Why the inconsistency is not merely cosmetic here:** a client facing two shapes
branches on shape rather than meaning. Once it does, key-counting distinguishes
`{detail}` from `{detail, problems}` — reintroducing exactly the oracle the flat
form exists to prevent. Format drift becomes an information leak.

This paragraph now belongs in the constraint layer of every task package that
touches an endpoint.
