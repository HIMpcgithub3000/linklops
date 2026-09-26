# Module 04 — Authentication & Authorization · complete, report 10/10

## DECIDE: A, API key

Chosen on **revocation**, not simplicity. A key is a database row, so revoking it is an
UPDATE effective on the *next request*. Revoking a JWT before its TTL needs a denylist
consulted per request — which is the per-request state a JWT exists to avoid, so you pay
its complexity and get a session's operating profile.

Product-specific: the abuse case is one tenant mass-producing phishing links on a *shared*
hostname. A fifteen-minute revocation delay is fifteen minutes of damage to every other
tenant on that domain.

Would switch to JWT for browser logins (short-lived credentials that expire on their own)
or a second service that must verify callers without asking this one. Neither exists.

## BUILD

`0007_api_keys.py`, `app/auth.py`, `app/ratelimit.py`.

- **sha256 hash + separate non-secret `key_id`** as the lookup handle. Without a handle,
  verification means scanning every row and hashing against each: O(n) per request, and the
  scan leaks timing so constant-time compare becomes pointless.
- **Comparison runs against a dummy hash on miss**, so an unknown `key_id` costs the same as
  a known one with a wrong secret. Otherwise 401 latency enumerates valid ids.
- **One identical 401 across five failure modes** — absent, malformed, unknown, wrong
  secret, revoked. Distinguishing them confirms half a credential, or that a key existed.
- **`get_tenant_session` takes the principal as an argument**, so the session cannot be
  constructed before auth returns. The alternative — stashing it on `request.state` — rests
  an authorization boundary on FastAPI's sibling-dependency resolution order.
- `AUTH_BACKEND` is now `api-key`; the Module 01 boot guard written to remove itself has fired.

**Rate-limit matrix**, keyed by what each limit protects:

| surface | key | limit | protects against |
|---|---|---|---|
| auth failures | key_id | 10/min | credential brute force |
| create | tenant | 60/min | abuse volume → shared-domain blocklist |
| redirect | client IP | 600/min | short-code enumeration |
| read | tenant | 120/min | bulk scraping |

Fails **open** on Redis loss — an abuse control that takes the service down when its own
dependency fails has become an outage. Redirect keys on the socket peer, **not**
`X-Forwarded-For`: with no trusted proxy that header is client-supplied, and a rate-limit
key an attacker chooses is not a rate limit.

## BREAK — found in my own code, one hour old

No planted IDOR; isolation held on all three routes (404 cross-tenant, 1 item in each of
B's two list paths). The real finding: **`api_keys` had no RLS** while every other tenant
table does, so the app role could read every tenant's `secret_hash`.

**Why RLS structurally cannot fix it:** `api_keys` is read *before* a tenant is bound,
because it is the query that *establishes* the tenant. A policy on `app.tenant_id` returns
zero rows and breaks every request. **The table that establishes identity is the one table
the identity mechanism cannot protect.**

## FIX — 0008

`verify_api_key(text)` and `touch_api_key(text)`, both `SECURITY DEFINER` with
`SET search_path`, `EXECUTE` revoked from PUBLIC then granted to `upsk_app`; `SELECT` and
`UPDATE` on `api_keys` revoked from the app role entirely.

Same pattern as `resolve_link()` for the public redirect — the system now has exactly two
audited privilege elevations, both narrow, both greppable. The function deliberately does
**not** compare the secret: a SQL comparison short-circuits and leaks timing, so hashing
and `hmac.compare_digest` stay in `app/auth.py`.

Verified: direct `SELECT` as `upsk_app` → `InsufficientPrivilege`, while auth, revocation
and `last_used_at` all keep working.

## Carry-forwards

- **Revocation is not the end of an incident.** Cutting a key stops future requests and does
  nothing about what already leaked — links created with it still resolve. Runbook is
  revoke, *then audit what that `key_id` did*. `last_used_at` exists for that; an
  append-only audit of key-authenticated writes is the obvious next control.
- Generalise `check_rls.py` (see STATE.md) — this module is the third finding it would have
  caught for free.
