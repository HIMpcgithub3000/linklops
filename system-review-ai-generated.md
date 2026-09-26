# LinkOps — System-Level Security & Architecture Review

*Reviewing AI-generated feature code as a **system**, not file-by-file. Scope
decision: AI-assisted first-pass sweep (the standing controls) + manual review
on the critical path (auth, authorization, mutation, and the five categories a
scanner structurally cannot judge). Every row below was verified against the
running code, not inferred.*

## Auth-check audit (every endpoint: identity **and** permission)

| Endpoint | Identity | Authorization | Status |
|---|---|---|---|
| `POST /links` | `require_principal` (API key → tenant) | RLS: writes scoped to bound tenant | OK |
| `GET /links` | `require_principal` | RLS on `links` | OK |
| `GET /links/search` | `require_principal` | RLS + allowlisted sort + bound params | OK |
| `GET /links/{id}/analytics` | `require_principal` | RLS on `analytics` (via `links`, migration 0010) | OK |
| `PATCH /links/{id}` | `require_principal` | RLS: `get_link` returns None cross-tenant | OK (proven by `test_tenant_cannot_touch_another_tenants_link`) |
| `GET /links/{id}` | `require_principal` | RLS; 404 for another tenant's id | OK |
| `GET /r/{code}` | public by design | `get_public_session` + `resolve_link` SECURITY DEFINER; tenant is an *output* | OK |
| `POST /teams/{id}/invitations` | `require_principal` | `require_role(admin)` — **application-level only** | **CRITICAL** (see F1, F2) |
| `POST /invitations/{token}/accept` | `require_principal` | token + email + read-time checks | **CRITICAL** (F1) |
| `DELETE /teams/{id}/invitations/{id}` | `require_principal` | `require_role` | **CRITICAL** (F1) |

The links plane is genuinely sound: **every** endpoint pairs `require_principal`
(identity) with `get_tenant_session` (authorization enforced by the database, not
the handler). The teams plane is where the findings are.

## Input-validation audit

| Endpoint | Field | Validation | Status |
|---|---|---|---|
| `POST /links` | `long_url` | `url_policy` (scheme, control chars, ReDoS-bounded) | OK |
| `POST /links` | `tags` | ≤20 items, ≤64 chars, control/bidi rejected | OK |
| `POST /links` | body | `extra="forbid"` — `tenant_id`/`created_by` rejected | OK |
| `PATCH /links/{id}` | `long_url` | **same** validator as create (no create/update drift) | OK |
| `GET /links/search` | `sort` | **allowlist** (identifier can't be bound) | OK |
| `GET /links/search` | `q`, `tag` | bound params, wildcards escaped | OK (was the M08 injection; fixed + regression-tested) |
| `POST /teams/{id}/invitations` | `role` | `role not in ROLE_RANK` → 400 | OK — **note:** LinkOps does *not* have the classic "no enum check on role" bug |

## IDOR audit (resource id → ownership check)

| Endpoint | Resource id | Ownership check | Status |
|---|---|---|---|
| `GET/PATCH /links/{id}` | `link_id` | RLS filters the row before the handler sees it → None | OK |
| `GET /links/{id}/analytics` | `link_id` | RLS on `analytics` scoped through `links` | OK |
| `accept_invitation(token)` | token | single-use `UPDATE … WHERE accepted_at IS NULL` | OK (concurrency-safe) |
| teams-table reads | team/membership ids | **`require_role` only — no RLS backstop** | **CRITICAL** (F2) |

---

## Prioritized findings

### F1 — CRITICAL: the entire teams/invitations feature is unimportable dead code

`app/routers/teams.py` does `from app.auth import Principal`, but **`app.auth`
defines no `Principal`** — `require_principal` returns a `str`. Verified:
`python -c "from app.routers import teams"` raises
`ImportError: cannot import name 'Principal'`. The router is also **not included**
in `app/main.py`. So today the feature is inert; the moment someone wires it in
(the obvious next step), **the app fails to boot**.

This is the review category "architectural coherence" plus a latent
business-logic gap: a whole feature exists, passed file-level review, and cannot
run. An AI file-review marks each function OK because each function is fine — the
defect is in how they compose with a type that doesn't exist.

**Fix:** define `Principal` (or change the annotations to `str` and pass the
tenant id through), then wire and test the router before it ships.

### F2 — CRITICAL: teams / team_memberships / invitations have no row-level security

`check_tenant_coverage.py` exits 1: these three tenant-scoped tables have
`enabled=False, policies=0`. Unlike `links` and `analytics`, their tenant
isolation rests **entirely on `require_role` in application code**. That is the
exact "security in context" failure the module names: `require_role` is correct
in isolation, but any future query on these tables that forgets to call it — or
any injection that reaches them, as the M08 tag-filter bug did — reads across
tenants with no database backstop. The links plane has two walls (RLS + handler);
the teams plane has one, and it's the weaker one.

**Fix:** a migration adding a `tenant_isolation` policy (USING **and** WITH
CHECK, FORCE) to each, scoped by `tenant_id` directly (`teams`) or through the FK
(`team_memberships`, `invitations`) — the same shape as migration 0010 for
`analytics`. Then `check_tenant_coverage` goes green and can join CI.

### F3 — REVIEW (business logic): is `admin` the right floor to invite?

`create_invitation` enforces `require_role(admin)`. Whether the product intends
admin-only, owner-only, or member-allowed invites is a spec question no AI or
scanner can answer. Flagged for product confirmation, not a code change.

## What the review did **not** find (and why that's the real result)

The categories where AI-generated code usually fails — missing IDOR checks,
create/update validation drift, PII in logs, non-idempotent writes under
concurrency — are **absent on the links plane**, because they were each found and
closed with a standing control earlier this cycle (RLS, `check_cache_scoping`,
`check_log_pii`, the analytics `ON CONFLICT`). That is the point of the hybrid
scope decision working as intended: the tireless mechanical checks became
controls that run every commit, freeing this manual pass to concentrate on the
one place they don't yet reach — the teams plane — where both criticals live.

## Recommended order of work

1. **F2** — add RLS to the teams tables (closes the data-layer risk even while
   the router is dead; makes `check_tenant_coverage` CI-able).
2. **F1** — fix `Principal`, wire the router, add IDOR + role tests mirroring
   `test_search`/`test_integration`.
3. **F3** — confirm the invite-permission floor with product.
