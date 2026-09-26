# Module 03 — Core API & CRUD · complete, report 10/10

## DECIDE: A, prefix route `/r/<code>`

Two saved characters is all B buys. It pays with a one-way door: a root route makes the
code namespace and the route namespace the same namespace, so every future path becomes a
reserved word that must be carved out of the code space *before* someone mints a colliding
code. `/health`, `/ready`, `/links` exist today; Modules 04-10 each add more, and under B
every one is a migration against live data whose fallback is breaking an issued link.

Deciding factor, same frame as Module 01: **reversibility**. A→B is additive later (serve
the root as a fallback, keep `/r/` working); B→A breaks every link already issued.

Secondary: `/r/` makes the system's single tenant-free read path visible at the edge, where
rate limiting, cache policy and abuse logging attach.

## BUILD

`app/routers/links.py` (POST/GET `/links`, GET `/links/{id}`), `app/routers/redirect.py`
(GET `/r/{code}`), `app/schemas/link.py`, `app/services/links_service.py`,
`app/services/url_policy.py`. Added `PUBLIC_BASE_URL` across the config contract — the
origin in a response is configuration, never the request `Host`, or a caller is handed a
`short_url` on their own domain for a real code in our database.

Nothing in the service layer filters by `tenant_id`. RLS does it. A redundant `WHERE` would
leave the policy untested by this path, which is how Module 02's drift would have gone
unnoticed here too.

## VERIFY — the three answers

- **Why separate the redirect from the admin API.** Two authorization planes: `tenant_id`
  is an INPUT on `/links` (tenant-bound session, RLS-scoped) and an OUTPUT on `/r/`
  (untenanted session that can read nothing from `links`, reaching one row through the
  `SECURITY DEFINER` `resolve_link()`). One namespace would put the system's only
  privilege-elevating read beside routes assuming the opposite.
- **Status for a validation error: 400**, deliberately overriding FastAPI's 422 — a WebDAV
  code intermediaries handle inconsistently. 422 is more precise about *where* it failed;
  one code for one condition beats precision the caller cannot act on. The body drops
  pydantic's `input` field: this endpoint rejects hostile URLs for a living, so reflecting
  the value puts those bytes in the response and the access log.
- **Expired link: 404**, byte-identical to unknown/disabled/suspended. Not 410 — 410
  confirms the code existed, which is an enumeration oracle. Honest cost: 410 is better for
  crawlers. Codes are credentials here, so enumeration wins the trade.

## BREAK — attacked my own boundary, found three real bypasses

Not a planted bug. Probed `url_policy` with near-misses; three were accepted and live:

```
https://good.com%40evil.example.com/login  -> browser decodes %40 to '@'; host is evil.example.com
http://127.1/admin                         -> inet_aton expands to 127.0.0.1
http://127.0.1/admin                       -> same
```

`[::ffff:127.0.0.1]` was correctly refused — Python's `is_loopback` does handle the mapped
form, so no overclaim there.

## FIX — one root cause, not three patches

**The security decision was being made by a parser that is not the parser which acts on the
value.** `urlsplit` decided; the browser dereferences. Wherever the two disagree about the
same string, my conclusion is void.

- Reject percent-encoding anywhere in the authority. Stronger than decode-and-recheck,
  which invites "how many times do I decode" — any answer below "until it stops changing"
  is another bypass.
- Delegate IP-literal detection to `socket.inet_aton`, the algorithm the resolver actually
  applies, instead of enumerating spellings I happened to think of.

**Controls.** `scripts/check_url_policy.py` — 43 cases, 34 refused, rows marked REGRESSION
are ones once accepted. No DB, no network, so it belongs in CI on every commit. Proved it
fires: dropping the percent check fails 1 case, dropping `inet_aton` fails 6, exit 1 each
time naming the row, exit 0 after repair.

**The half that is not code.** `scripts/audit_stored_destinations.py` re-validates every
stored destination against the current policy — a create-path fix does not touch rows
already written, and those are exactly the ones an attacker made on purpose. Found 6 links
written while the hole was open, `--disable` set `disabled_at`, all three exploited codes
now 404. Disable rather than delete: the code stays claimed so it cannot be reissued, and
the row survives for whoever explains the incident. Exits 0 once nothing resolves — a
permanently red control is one everyone learns to scroll past.

## The API design rule

State a validation rule in terms of the parser that will **act** on the value, not the one
convenient to call. Where two readings of the same string differ, reject rather than
normalise — "normalise to whose reading?" has no safe answer. Where a real algorithm exists
for the question (`inet_aton`, punycode), delegate to it.

Corollary, which cost me the second half of this fix: tightening validation has two halves
— stop accepting it, and go find what was already accepted.

## Callback correction

The canned callback claims the bug followed from the prefix route needing careful
controller ordering. It did not. `/health` and `/ready` answered 200 throughout and no
route ever collided. Choosing the prefix *removed* the collision class, which is why the
bug that did happen was in the one place a namespace boundary cannot help: what a URL
means, not where a route lives.

## Carry-forwards

Recorded in `progress/state.json` under `bootcamp.progress.module_03.carry_forward` —
M04 retires the dev tenant header and nil `created_by` (which retires the `AUTH_BACKEND`
boot guard) plus create-side rate limiting; M05 must not let structured logging re-add the
dropped `input`; M06 caching the redirect reintroduces the expiry window that read-time
evaluation currently avoids; M09 wire `check_url_policy.py` into the suite; M10 run all
three controls in CI.
