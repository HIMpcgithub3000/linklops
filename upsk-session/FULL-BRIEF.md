# upsk bootcamp — session handoff brief

Paste everything below the line into the new session as your first message.

---

You are picking up an in-progress session. Read this entire brief before running anything.

## The arrangement

I'm Himanshu Sharma, enrolled in the upsk.to AI engineering bootcamp (`upsk` CLI, skill
`system-design-fundamentals`, track FastAPI, product scenario `b2b_multi_tenant`). The
bootcamp's AI instructor runs in a **separate** session and owns the `upsk` CLI state.

Your role: you're on my side of the table. I paste the instructor's step content to you,
you produce the engineering answer and the code that backs it. Rules:

- **Never run state-changing upsk commands** — `upsk next`, `upsk decide`, `upsk report`,
  `upsk hint`. The instructor session owns those. `upsk status --json` (read-only) is fine
  if you need to confirm where we are.
- Answer style: concise, no filler, no restating the question back at me. Lead with the
  claim, then the evidence. Strong-intermediate engineering register — not a fresher, not
  a staff engineer performing. Go long only when the step actually needs it.
- Every claim gets verified against the running system, not asserted from documentation.
  If you say an index is used, show the `EXPLAIN`. If you say a control fires, re-inject
  the failure and show it exit non-zero.

## Environment

| | |
|---|---|
| Workspace | `/Users/himanshusharma/caw/upsk-system-design-workspace` |
| API root | `api/` (Module 01 decision A: single-app repo) |
| Platform | macOS arm64, zsh, Python 3.14.6 |
| Venv | `api/.venv` — always invoke as `.venv/bin/python`, `.venv/bin/alembic` |
| Postgres | Docker container `upsk-sdf-postgres`, postgres:16, host port **55432** |
| Database | `upsk_sdf`, owner role `upsk_owner`, separate non-owning app role for RLS |
| Migrations | alembic, currently at `0003 (head)` |
| Deps | fastapi 0.141.1, uvicorn[standard] 0.52.1, pydantic-settings 2.15.0, SQLAlchemy 2.0.51, psycopg[binary] 3.3.4, alembic 1.19.1 |
| Public profile | upsk.to/himpcgithub3000 |

Secrets live in `api/.env` (gitignored). Never print its values into an answer, an
evidence file, or a commit — Module 01's one recorded growth area was a credential leak.

## File map

```
api/
  app/config.py      pydantic-settings Settings, fail-fast validation, three guards,
                     key-parity check, credential redaction on the error path
  app/db.py          engine, sessionmaker, request-scoped tenant binding (sets the
                     app.tenant_id GUC per request). AUTH_BACKEND = "dev-header",
                     X-Dev-Tenant-Id, with a startup guard that refuses to boot if
                     ENVIRONMENT != development. Module 04 replaces this with a JWT claim.
  app/models.py      SQLAlchemy 2.0. Link, ClickEvent. UUIDv7 time-ordered PKs (not uuid4 —
                     random keys scatter B-tree inserts and cause page splits).
  app/main.py        GET /health (dependency-free), GET /ready (hard deps, explicit timeout,
                     503 so the instance deregisters instead of being killed)
  alembic/versions/0001_initial_schema.py
                     0002_rls_empty_guc.py
                     0003_restore_with_check.py
  scripts/config_drill.sh          Module 01 config failure drill
  scripts/module-02-seed-query.py  seeds two tenants, runs the four negative controls,
                                   writes progress/evidence/module-02/query-by-code.txt
  scripts/check_rls.py             asserts live RLS matches the migrations. exit 0/1.
  .env / .env.example              PORT, ENVIRONMENT, DATABASE_URL, MIGRATION_DATABASE_URL,
                                   MONGODB_URI, REDIS_URL, JWT_SECRET, API_KEY_A, API_KEY_B
progress/state.json                decisions + per-module progress + carry-forwards
progress/evidence/module-02/query-by-code.txt
```

## The data model argument (repeat this framing, it's load-bearing)

Two planes, and `tenant_id` plays opposite roles in each:

```
data plane (redirect)      WHERE code = ?        tenant_id is an OUTPUT
                           public, unauthenticated, carries no tenant context,
                           so `code` must be globally unique — the lookup
                           resolves the owner rather than filtering by it

management plane (CRUD)    WHERE tenant_id = ?   tenant_id is an INPUT
                           supplied by the caller's identity, constrains what
                           may be seen
```

Same column, opposite direction — which is why they need different indexes rather than one
compromise index serving neither. `click_events` is RANGE partitioned by `clicked_at`.

RLS predicate on both `links` and `click_events`, `FORCE ROW LEVEL SECURITY`, applied to a
dedicated non-owning app role:

```sql
tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
```

## Progress

Launchpad pack, 10 modules, 18% complete.

```
[x] 01 Setup & Clean Code
[>] 02 Database Design & Selection   <-- here, step BREAK
[ ] 03 Core API & CRUD
[ ] 04 Authentication & Authorization
[ ] 05 Error Handling & Logging
[ ] 06 Caching With Redis
[ ] 07 Background Jobs & Analytics
[ ] 08 Search & Advanced Queries
[ ] 09 Testing
[ ] 10 CI/CD & Deployment (Railway)
```

### Module 01 — complete, 5/5, zero hints, all five dimensions at 5

DECIDE: **A, single-app repo**, `API_ROOT = api/`. Reasoning: the roadmap produces one
service with two entrypoints, not two deployable services — M4 auth is middleware, M5
tenant scoping is in-process, M6 caching is infrastructure, M7 purge is a scheduled job
over the same models. Types shared by import, so the API and the purge worker cannot hold
two versions of tenant scoping at once — a worker on stale scoping deletes the wrong
tenant's rows. Deciding factor was reversibility asymmetry: A→B is a `git mv` plus a CI
fix verified by tests; B→A never happens, because nobody refactors toward less structure.

Its BREAK was `APP_PORT` vs `PORT` — a config key that existed in `.env` under a name
`Settings` never read. The lasting correction (mine, and the instructor conceded it): the
missing boundary was between **the contract and its consumer**, not between apps. `.env`
and `Settings` were independent artifacts with nothing binding them.

Carry-forwards that are still open:

1. Extend key parity to a third direction — `.env.example` against the `Settings` model —
   to catch a field added in code and never documented.
2. A bare required `str` still accepts an empty value. Closing that needs `MinLen` or a
   format type such as `PostgresDsn`.
3. `/health` stays dependency-free; `/ready` covers hard dependencies only.
4. **Deferred, still owed:** readiness must flip false on SIGTERM *before* the server
   closes, or routine deploys drop in-flight requests.
5. Known rough edge, deliberately unfixed: `difflib` suggests a mildly wrong "did you
   mean" key. The authoritative line above it names the missing key exactly, so the hint
   is advisory.

### Module 02 — in progress, step BREAK

DECIDE: **A, PostgreSQL.** The reasoning that won it — query shapes are engine-neutral on
retrieval (code lookup is a unique-index hit either way, tenant-scoped list is a compound
index scan either way), so the *constraints* decide, not the queries:

- Uniqueness is a property of the data, not of the code that writes it. `NOT NULL UNIQUE`
  means a link with no code cannot exist. A document omitting the field inserts happily,
  and unique indexes treat missing as null — the second such write fails with a confusing
  duplicate-key-on-null.
- Decisive for `b2b_multi_tenant`: row-level security is the only mechanism in either
  engine that makes the Module 01 claim literally true — that isolation is structural
  rather than remembered. With `FORCE ROW LEVEL SECURITY` and a dedicated non-owning app
  role, a handler that forgets the tenant filter returns **zero rows** instead of
  **everyone's rows**. Mongo has no per-row equivalent; views are read-only, so writes
  still depend on discipline.

BUILD and VERIFY are done. Evidence in `progress/evidence/module-02/query-by-code.txt`:
insert + select round-trip under a tenant session, four negative controls, the public
redirect path resolving without a tenant bound, and a click event landing in the correct
`click_events_2026_08` partition.

**BREAK — already diagnosed and fixed, but `upsk next` was never run, so the instructor
still shows step = `break`.**

The injected bug: `ALTER POLICY tenant_isolation ON links WITH CHECK (true)`, run by hand
directly against the database. `qual` was untouched, so reads stayed sealed — which is why
three of the four negative controls still passed and only the write-side control caught it.

Why that shape is the dangerous one: losing `WITH CHECK` while keeping `USING` means tenant
B writes rows into tenant A's account and then cannot see them. The injected data is
invisible to B, invisible to every read-side test, and surfaces only in A's dashboard as
records nobody there created. Silent cross-tenant write injection. A read leak gets
noticed; this doesn't.

Why the repo looked innocent: it *was* innocent. The migration file was unmodified and
`alembic_version` read `0002` accurately the whole time. Alembic records **which revisions
ran**; it never verifies the schema still matches them. Hand-run DDL is invisible to it
permanently — so "git is clean and we're at head" is not a statement about the database.

The fix, both parts already shipped:

- `alembic/versions/0003_restore_with_check.py` — drops and recreates `tenant_isolation` on
  both tables with `USING` *and* `WITH CHECK` set to the tenant predicate.
- `scripts/check_rls.py` — asserts the live config matches the migrations: RLS enabled, RLS
  **forced**, and both `USING` and `WITH CHECK` scoped. Verified it actually fires by
  re-injecting the identical drift:

```
healthy   -> RLS ok: links, click_events                                        exit=0
drifted   -> RLS DRIFT: links: WITH CHECK is not the tenant predicate -> 'true' exit=1
repaired  -> RLS ok                                                             exit=0
```

The generalisation to carry into later modules: a migration proves the schema was *once*
correct. For schema that is a security control, something has to assert it's *still*
correct — on boot or in CI. `alembic upgrade head` returning clean is not that assertion.

## How I'm taught (match this)

Instructor calibration from the pretest — I scored ~9/10 across six technical questions
and the instructor explicitly set: skip basic definitions, ask "what breaks in production"
after every VERIFY, select harder BREAK bugs earlier.

Two tracked gaps to actively work against:

1. **I under-weight the time axis.** I reason spatially about current state, less about
   what changed and when. Push me toward onset time, correlation IDs, "what was true an
   hour ago".
2. **I cost a mechanism correctly but don't always verify the intended benefit lands.**
   I'll argue the index correctly and not check the query planner actually chose it. Make
   me prove the benefit, not just the reasoning.

Recorded strengths to keep using: unprompted security reasoning, naming concrete failure
modes rather than abstractions, framing tradeoffs as purchases with a price.

Recorded style signals: I respond to adversarial challenge rather than explanation, and
I push back with evidence when a claim is overstated — including on the instructor's own
evaluation, which I've done successfully. I verify empirically rather than reasoning from
documentation. Don't soften. If my answer is wrong, say so and show the counterexample.

Module 01's one recorded growth area: the validator's error printer echoed the raw error
input, and a missing-field error carries the parent object rather than the absent field —
so the required-field path printed the entire collected settings dict to stderr, exposing
two database passwords, a cache password, the token signing key and both tenant API keys,
on the path most likely to fire in a fresh deployment. Fixed, but it's on the record.
Treat any error path that touches config as a redaction question first.

## State right now

Verified at handoff time:

- `upsk-sdf-postgres` up, port 55432
- `.venv/bin/alembic current` → `0003 (head)`
- `.venv/bin/python scripts/check_rls.py` → exit 0
- upsk CLI still reports module 2, step `break` — the instructor is waiting on FIX

## Loose ends

- `git status` in the workspace shows `api/` and `progress/` untracked, **zero commits on
  `main`**. Module 10 is CI/CD and Module 09 is Testing; both assume history exists.
- Carry-forward #4 above (readiness false on SIGTERM) is still owed from Module 01.
- `progress/state.json` says `next_step: "context"` for module 2, which is stale — the
  authoritative step is whatever `upsk status` reports.

## First thing to do

Nothing. Confirm you've read this and wait — I'll paste the instructor's next step content.
