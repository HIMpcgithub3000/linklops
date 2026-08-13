# Module 01 Diagnosis Notes

Lab: LinkOps, forked from the System Design Module 03 URL shortener (the server routes this
session as `lane: checkpoint_fork`, so it is the same `api/` tree, not a fresh copy).
Infra: `infra/docker-compose.yml` — `linkops-postgres` on **5433**, `linkops-redis` on 6379.

## Bug 1

- **Symptom:** the service refuses to start. Expected (per the lab text) an ORM error along
  the lines of "cannot connect to the database". Got something different, which is the
  interesting part — see the observation under Hypothesis A.

- **Hypothesis A:** the process never got as far as connecting; configuration failed first.
  - Command: `.venv/bin/python -m uvicorn app.main:app --port 8099`
  - Observation:
    ```
    FATAL: configuration contract violated -- refusing to start
      missing key 'DATABASE_URL': declared in .env.example but supplied by
                                  neither .env nor the environment
      unknown key 'DB_URL' in .env: not part of the contract -- did you mean 'REDIS_URL'?
    ```
    Confirmed, and it settled the diagnosis in one command. No ORM was involved at all: the
    key-parity check in `app/config.py` runs at import, before any engine is constructed.
    Both halves of the rename are named — the key that vanished and the key that appeared.

- **Hypothesis B:** the database is genuinely unreachable (container down, wrong port).
  - Command: `docker compose -f infra/docker-compose.yml ps` and `pg_isready`
  - Observation: not needed as a *test* — Hypothesis A had already confirmed and the two
    are mutually exclusive. Recorded because it was the hypothesis I would have spent the
    next ten minutes on had the error been the one the lab expected. Checked afterwards
    anyway out of discipline: both containers healthy, Postgres accepting connections.

- **Fix:** restore the key name in `.env` from `DB_URL` back to `DATABASE_URL`. One line.
  The application code was never wrong.

- **Verification proof:** service boots; `/health` → `{"ok":true}`, `/ready` → 200.

**What this bug actually taught, which is not what it was designed to teach.** The lab
assumes the symptom is a *misleading* error — the ORM complaining about connectivity when
the real fault is a name mismatch — so the exercise is hypothesis generation under a lying
symptom. This codebase refused to produce that symptom. Three guards from System Design
Module 01 (key parity in both directions, `extra="forbid"`, and required fields) exist
precisely because of the `PORT` → `APP_PORT` incident, which is the identical bug class.
So the difficulty of a bug is not a property of the bug. It is a property of what the
system says when it fails.

**One thing that did not work.** The hint said *did you mean 'REDIS_URL'?* when the answer
is `DATABASE_URL`. `difflib` picked the wrong neighbour. That exact weakness was recorded
as a known rough edge in Module 01's carry-forwards — "the authoritative line above it
names the missing key exactly, so the hint is advisory" — and here it is, mildly wrong,
in a real incident. It cost nothing only because the line above it was correct. A hint
that is confidently wrong is worse than no hint for anyone who reads it first.

## Bug 2

- **Symptom:** `GET /links?page=1&limit=10` returns 10 links, but they are `test011`
  through `test020`. Seeds `test001`–`test010` appear on no page at all. Nothing errors,
  each page looks internally plausible, and the total across pages is 15 of 25.

- **Hypothesis A:** the offset is computed for 0-based pages while the API takes 1-based
  ones, so every page is shifted by exactly one page-width.
  - Command: request pages 1–3 at `limit=10` and diff the union against the seeded set.
  - Observation: confirmed. Page 1 = `test011..020`, page 2 = `test021..025`, page 3 empty.
    That is `offset = page * limit`: page 1 asks for offset 10. The missing rows are the
    first `limit` of them, which is the signature of this bug rather than of a filter.

- **Hypothesis B:** rows are missing because of a `WHERE` clause or RLS scoping, not paging.
  - Command: `GET /links?limit=100` (the cursor path, no `page` parameter).
  - Observation: ruled out — all 25 come back through the keyset endpoint against the same
    session and the same tenant. So the rows are visible; only this code path loses them.

- **Fix:** `offset = (page - 1) * limit`, and restore `ORDER BY created_at DESC, id DESC`
  on the query. Both, not either. The offset made the wrong rows appear; the missing
  `ORDER BY` made "wrong" unrepeatable — `LIMIT/OFFSET` without `ORDER BY` does not slice a
  sequence, it slices whatever order the plan happened to produce, and Postgres may choose
  differently between two executions with no concurrent writes at all.

- **Verification proof:** pages 1–3 at `limit=10` return 10 + 10 + 5 = **25 distinct codes,
  0 duplicates**, ordered `test025 … test001`.

## Bug 3 — not injected, found while verifying the others

- **Symptom:** every seeded link 404s on the public redirect. `GET /r/test001` → 404, while
  the row is present and `GET /links` lists it.

- **Hypothesis A:** the row fails a resolvability rule inside `resolve_link()` — disabled,
  expired, or a suspended tenant.
  - Command: query `links` and `tenants` for the seeded row directly.
  - Observation: ruled out. `disabled_at` null, `expires_at` null, tenant `active`.

- **Hypothesis B:** the request never reaches the lookup.
  - Command: read `CODE_PATTERN` in `app/routers/redirect.py`.
  - Observation: confirmed. The pattern was compiled from `links_service.CODE_ALPHABET`,
    which deliberately omits `0`, `1`, `l`, `I` and `O` as ambiguous glyphs. `test001`
    contains `0` and `1`, so the redirect rejected it before touching the database.

- **Fix:** `CODE_PATTERN = ^[A-Za-z0-9]{7,32}$`.

- **Verification proof:** `/r/test001` → 302, `/r/test002` → 302; queue depth 3 after three
  redirects; the worker drains it to 0 and `analytics` holds counts 2 and 1.

**Why this one matters more than it looks.** `CODE_ALPHABET` is a *generator* policy — a
statement about what we choose to mint. Compiling it into the *reader* asserts something
stronger and false: that no other code can exist. Any imported, vanity or legacy code
containing a `0` becomes permanently unresolvable while sitting in the table. The tell is
that changing the generator's alphabet would retroactively break every link already issued
under the old one. A validator describes what the column can hold; a generator decides what
we put in it, and only one of those belongs in a request path.

## Two process failures worth more than the bugs

**I debugged a stale server twice.** Both times I changed code, re-ran, saw the *old*
behaviour, and briefly believed the fix had not worked. Both times the truth was that an
earlier `uvicorn` still held the port, my new one failed to bind, and `curl` was answered
by the process I had not changed. The first instance cost a full evidence run on port 8000
(a three-day-old process I do not own); the second was my own survivor on 8099, 79 seconds
old, because `kill %1` referred to a job in a shell that had already exited.

The lesson is the module's own thesis pointed inward: I formed a hypothesis ("the fix is
wrong") and tested it against output, without first confirming the output came from the
thing I changed. The cheap guard is to make the server identify itself — check the bind
succeeded before trusting a single request.

**`lsof` said port 5432 was free; the bind said `EADDRINUSE`.** `lsof`, `netstat` and
`docker ps` all reported nothing holding it. Something does, invisibly to this user. A
socket bind is the only check that answers the question actually being asked; every other
check answers "can I *observe* a holder", which is weaker and quietly different.
