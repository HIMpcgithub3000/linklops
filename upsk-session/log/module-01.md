# Module 01 — Setup & Clean Code · complete, 5/5, zero hints

## DECIDE: A, single-app repo, `API_ROOT = api/`

The roadmap produces one service with two entrypoints, not two deployable services — M4
auth is middleware, M5 tenant scoping is in-process, M6 caching is infrastructure, M7
purge is a scheduled job over the same models. Types shared by import, so the API and
the purge worker cannot hold two versions of tenant scoping at once; a worker on stale
scoping deletes the wrong tenant's rows.

Deciding factor was **reversibility asymmetry**: A→B is a `git mv` plus a CI fix verified
by tests; B→A never happens, because nobody refactors toward less structure. B pays a
setup cost daily from Module 01 for a benefit contingent on a growth event this roadmap
doesn't predict.

Open risks named at decision time: single-app gives no free boundaries (mitigation is an
ESLint `no-restricted-imports` rule, not folder depth); both entrypoints share a config
module, so fail-fast validation must live there and not in `server.js`.

## BREAK

`APP_PORT` vs `PORT` — a config key that existed in `.env` under a name `Settings` never
read.

The lasting correction, mine, which the instructor conceded: **the missing boundary was
between the contract and its consumer, not between apps.** `.env` and `Settings` were
independent artifacts with nothing binding them. The module's canned Decision Callback
blamed the flat single-app structure and was wrong. This is the pattern that earned the
5/5 — the callback will be similarly off in later modules.

## Carry-forwards still open

1. Extend key parity to a third direction — `.env.example` against the `Settings` model —
   to catch a field added in code and never documented.
2. A bare required `str` still accepts an empty value. Closing that needs `MinLen` or a
   format type such as `PostgresDsn`.
3. `/health` stays dependency-free; `/ready` covers hard dependencies only. **This is the
   argument to reuse when anything is proposed for `/ready`** — see module-02.md.
4. **Still owed:** readiness must flip false on SIGTERM *before* the server closes, or
   routine deploys drop in-flight requests.
5. Known rough edge, deliberately unfixed: `difflib` suggests a mildly wrong "did you
   mean" key. The authoritative line above it names the missing key exactly, so the hint
   is advisory.

Public evidence on record: the three-guard config contract, fail-fast validation,
credential redaction, the health endpoint — each with verification attached.
