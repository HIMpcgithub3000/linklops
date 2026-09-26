# Module 05 — Error Handling & Logging

System Design Fundamentals, practitioner pack. BREAK/FIX cycle, hard difficulty.

## The injected bug

`app/services/teams_service.py`, `accept_invitation`. The five dead-token causes
(no such token / revoked / already accepted / expired / wrong recipient) all
return one identical 404 by design, so the caller cannot tell them apart. A
plausible operator-ergonomics change added a discriminating log line:

```python
log.warning("invitation rejected", extra={
    "reason": reason,
    "invited_email": row.email if row is not None else None,
    "caller_email": user_email,
})
```

Symptom, as it surfaced: nothing visible from outside. Status, body and the
indistinguishability of the five causes were all unchanged. What arrived was a
customer's security review of the log export — personal data present in a stream
covered by a narrower processing agreement.

## Why it is worse than "emails in a log"

Three distinct problems, only the first of which is obvious.

1. **Contact data in a stream with different handling rules.** The log is
   exported to customers, retained on a different schedule than the database,
   and readable by anyone with log access — a wider audience than the table the
   address came from, reached without a review.

2. **It reopens the enumeration oracle the identical 404 exists to close.** The
   response was carefully built to tell a stranger nothing. But anyone can POST
   a guessed token, and the `recipient_mismatch` path then records the address
   that token belongs to. The oracle moved from the response to the log; it did
   not close. `invited_email` is also contact data for a *third party* — someone
   who is not the caller and never made a request.

3. **Level inflation.** WARNING for an event any anonymous caller can trigger at
   will. By this project's own rule in `app/logging_config.py` — a level answers
   "would someone have to act on this?", which is why a 404 on an unknown short
   code is INFO — one dead token is the service working correctly. The alertable
   signal is the rate, not the line.

## The fix

Keep the operational value, drop the identifiers:

```python
log.info("invitation rejected", extra={
    "reason": reason,
    "invitation_id": str(row.id) if row is not None else None,
    "user_id": str(user_id),
})
```

`reason` survives — it is chosen from a fixed set inside the function and is not
derived from anything the caller sent, which is the property that makes it safe
to log. The two ids are opaque handles that resolve to a person only through an
authenticated admin lookup, so support keeps its answer to "why did this link
fail" without the log holding the answer to "who was invited".

## Second instance, found by running the control

Not injected — pre-existing, and reachable in production. `app/main.py`'s
`database_error` handler logged `exc_info=exc`. A psycopg constraint violation
renders as the full server message, and its `DETAIL` line quotes the *entire
failing row*:

```
DETAIL: Failing row contains (80b22153-…, …, priya.nair@northwind-legal.example, member, …)
```

Verified live: the emitted line was 6481 bytes and contained the address. On
`invitations` that means an email; on any future table it means whatever that
table holds.

Fixed without giving up diagnosis, because Postgres already separates the two.
`exc.orig.diag` exposes the failure as fields, and only `message_detail` carries
row values — so `message_primary`, `constraint_name`, `table_name` and
`column_name` are logged and the values are not. Python frames are kept
separately via `traceback.format_tb(...)[-4:]`, which renders *where* the
statement was issued without rendering the exception's text: the half of a
traceback with diagnostic value here, and the half that cannot carry a row.

`log.error("unhandled exception", exc_info=exc)` in the last-resort handler is
deliberately left alone. That is a different trade — an unknown exception type,
where the traceback is the only thing there is.

## Standing control added

`api/scripts/check_log_pii.py` — fifth in the family with `check_rls.py`,
`check_url_policy.py`, `check_log_injection.py`, `audit_stored_destinations.py`.
Pairs with `check_log_injection.py`: that one asserts a record cannot become two
lines (structure), this one asserts a record cannot become a copy of someone's
address book (content).

Installs a capturing handler rendering through the real `JsonFormatter`, drives
six refusal paths plus the driver-error handler against real Postgres, and scans
every emitted line with two detectors:

- **sentinels** — per-run unique addresses, matched whole, local-part-only,
  domain-only and case-insensitively. Catches PII that was reshaped on the way
  out; a generic pattern would miss `priya.nair` logged without its domain.
- **pattern** — a generic address shape. Catches PII that never passed through
  the script's arguments: a row read back from the database, a driver message
  quoting a parameter.

A scenario that stops refusing is recorded as a failure, not a pass — otherwise
the control would report clean logs for a path it never drove.

**Watched failing, both instances:**

| reintroduced | exit | reported |
|---|---|---|
| `invited_email` / `caller_email` in `dead()` | 1 | 4 refusal paths, on the domain fragment |
| `exc_info=exc` in `database_error` | 1 | driver-error handler |

Fixed state: exit 0, 6 refusal paths + the driver-error handler.

**Not wired into CI yet.** It needs the database, which CI has (Postgres service
container), so wiring is a `ci.yml` edit and not a design question — but it has
not been made, and until it is this runs by hand like `check_tenant_coverage.py`
and `check_redos.py`.

## Full state at close

`check_log_pii` 0 · `check_log_injection` 0 · `check_url_policy` 0 (43 cases) ·
`check_rls` 0 · `audit_stored_destinations` 0 (1958 links) · pytest 20 passed ·
ruff clean (10 pre-existing `UP017` fixed across `activity.py`,
`teams_service.py`, `check_log_pii.py`).

## Carry-forward

- `check_log_pii.py` is not in `ci.yml`. Neither are `check_tenant_coverage.py`
  (exits 1 — `teams`, `team_memberships`, `invitations` have no RLS) or
  `check_redos.py`.
- `app/routers/teams.py` imports `Principal` from `app.auth`, which does not
  define it, and `main.py` never includes the teams routers. The invitation
  endpoints are unreachable over HTTP — which is why this module's evidence runs
  at the service layer. Pre-existing, unrelated to this bug.
- Environment correction: the live database is container `linkops-postgres` on
  **:5433**, not `upsk-sdf-postgres` on :55432. The latter exists and is empty.
