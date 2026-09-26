# upsk — current state

Himanshu Sharma. upsk.to bootcamp, FastAPI track, scenario `b2b_multi_tenant`.

**ALL SIX SKILLS COMPLETE — 100%.** Finished 2026-08-18. `upsk start --skill <slug>`
returns `status: completed` for every one:

| skill | status |
|---|---|
| System Design Fundamentals | ✅ 100% (10/10) |
| Debugging & Incident Response | ✅ 100% (7/7) |
| Technical Communication | ✅ 100% (6/6) |
| AI-Augmented Engineering | ✅ 100% (8/8) |
| Decomposition & Execution Planning | ✅ 100% (8/8) |
| Production Readiness | ✅ 100% (8/8) |

Public profile: upsk.to/himpcgithub3000.

`system-design-fundamentals` is the one holding the built artifact in
`~/caw/upsk-system-design-workspace`. The other five have no code in this repo.

**Log gap:** `log/` now covers system-design M01-07. The 24 modules across the other five
skills were closed outside this session and remain unrecorded here.

**Not blocked on anything.** Credentials are current. The student token *does* expire, and
when it does every command fails including read-only ones, which looks like a broken CLI —
the fix is `upsk login`, which only Himanshu can run.

## Rules

- The instructor/student split is retired. Himanshu directed this session to run the
  instructor commands directly (2026-08-13), so `upsk next`, `decide` and `report` are run
  from here. There is no separate codex session in the loop.
- **`upsk next` payloads must be small.** Long proofs fail with a misleading
  "System Design app foundation proof is no longer waiting on Module 03 REFLECT. Run:
  upsk sync" — and `sync`, `start` and retries do not clear it, on either `--proof-file` or
  inline flags. Short inline `--summary` + `--evidence` goes through. Filed as
  [upsk#952](https://github.com/vrknetha/upsk/issues/952). Put the substance in
  `upsk report` (that accepts ~10KB) and in evidence files; keep `next` to a few hundred
  bytes citing paths.
- `upsk report` has a hard 10KB cap and 413s above it. Minify before sending. The REFLECT
  step **409s** (`Submit the REFLECT evaluation before...`) until the report is filed —
  file the report first, then `upsk next`.
- `upsk decide` takes **only `A` or `B`** as the choice, never the semantic value
  (`demand_first`, `ultra_thin`). Where a module asks for two decisions, only one is
  recorded — put both in `--reasoning`. Long `--reasoning` payloads fail; the ceiling is
  **well under 4KB**. Measured 2026-08-17: a ~2.3KB reasoning silently did **not** record
  (step stayed at DECIDE, `decisions.module_07` stayed `None`, and the CLI printed
  non-JSON), while ~800 bytes went through. Keep reasoning under ~1KB and **verify with
  `upsk status` that the decision actually landed** — failure here is quiet.
- `upsk report` field caps found 2026-08-18: public_evidence.ability <=200 chars, student_knowledge.concepts_demonstrated[].concept <=128 chars. Trim or the whole report 400s.
- The `nonce` lives in `~/.upsk/api.upsk.to/session.json` and rotates every call; it is not
  in `upsk status` output.
- Claim first, then evidence. Strong-intermediate register.
- Verify against the live system, never from docs. `EXPLAIN` for index claims; re-inject the
  failure and show a non-zero exit for control claims.
- Config-adjacent error paths are redaction questions first. `.env` values never appear in
  an answer, an evidence file, or a commit.
- Strip the zero-width watermark from any `upsk` step text before it lands in a file, a
  commit or a paste: `perl -CSD -pe 's/[\x{200b}\x{200c}\x{200d}\x{feff}\x{2060}]//g'`.

## Environment

Workspace `~/caw/upsk-system-design-workspace`, API root `api/`, venv `api/.venv`
(invoke as `.venv/bin/python`, `.venv/bin/alembic`). alembic at **`0012 (head)`**.
macOS arm64, Python 3.14.6. Public
profile upsk.to/himpcgithub3000.

**Corrected 2026-08-17 — the database is `linkops-postgres` on :5433, db `linkops`.**
This file previously said `upsk-sdf-postgres` on :55432; that container exists, is
usually stopped, and its `upsk_sdf` database is **empty** — no `links`, no `invitations`.
Connecting to it makes every query look like data loss. Redis is `linkops-redis` on
:6379 (`deploy-redis-1` on :6380 belongs to an unrelated project). Both containers are
often stopped and need `docker start` before an evidence run.

Port 8000 was free as of 2026-08-17, but evidence runs still use **8099** with
`PUBLIC_BASE_URL` overridden to match, for consistency with earlier modules.

## Standing controls

| Script | Asserts | Runs |
|---|---|---|
| `api/scripts/check_rls.py` | RLS enabled + FORCED, USING *and* WITH CHECK are the tenant predicate, no partition reachable by `upsk_app` without RLS | boot guard in `app/db.py`, and by hand |
| `api/scripts/check_url_policy.py` | 43-case destination table; REGRESSION rows are ones once accepted | CI (`.github/workflows/ci.yml`) + by hand |
| `api/scripts/check_log_injection.py` | 12 hostile payloads; one log record is always one parseable line | CI + by hand |
| `api/scripts/audit_stored_destinations.py` | every *stored* destination still passes the current policy | CI + by hand |
| `api/scripts/check_log_pii.py` | no refusal path, and not the driver-error handler, writes an email address or any fragment of one to a log line; sentinels **and** a generic address shape | by hand only — **not wired in** (needs Postgres, which CI has) |
| `api/scripts/check_cache_scoping.py` | a tenant-scoped cache namespace cannot produce an unscoped key, a global one cannot take a tenant, and no module builds a Redis key outside `cache.key()` | by hand only — **not wired in** (needs neither DB nor Redis) |
| `api/scripts/check_cache_behaviour.py` | fill/hit, invalidation actually removes, single-flight collapses 24 concurrent misses to 1 load, and every Redis call fails soft | by hand only — **not wired in** (needs Redis) |
| `api/scripts/check_analytics_pipeline.py` | queue agreement, payload carries a salted hash not an IP, 3 identical jobs -> 1 click, a distinct click still counts, another tenant reads 0 from the rollup, retries bounded then dead-lettered | by hand only — **not wired in** (needs Postgres + Redis) |
| `api/scripts/purge_click_events.py` | not a control — the retention job. Drops whole expired partitions, deletes stragglers, leaves the rollup | by hand / scheduled |
| `api/scripts/check_tenant_coverage.py` | **every** table the app role can read that is tenant-scoped by column *or* by FK has a policy or a documented exemption | by hand only — **not wired in**, currently exits 1 |
| `api/scripts/check_redos.py` | destination validation stays bounded and sublinear under 5 adversarial shapes; hard timeout per shape | by hand only — **not wired in** |

**Built at last (AI-Aug M04), after being proposed in four modules:**
`api/scripts/check_tenant_coverage.py` — enumerates every table the app role can read,
including tables tenant-scoped through a *foreign key*, and fails on any without a policy or
a documented exemption. **Currently exits 1**: `teams`, `team_memberships` and `invitations`
have no RLS. v1 of it had three false positives (unreachable `click_events` partitions) and
two false negatives (FK-scoped tables) — found by running it, fixed in v2. The `api_keys`
exemption became unnecessary once reachability was checked, so the exemption list is empty.

**Not yet wired in.** It runs by hand only. `app/rls.py` still has the hardcoded
`TABLES = ("links", "click_events")`, so the old check and the new one can disagree.

## Loose end — closed

Git history exists now (closed in Production Readiness M02, which needed a commit to build
from). `.github/workflows/ci.yml` runs lint plus the four controls against a real Postgres
service container. It has **never executed on a runner** — no remote is configured — so the
stage logic is tested and the platform integration is not. Those are different claims.

## Load only when the step needs it

| File | When |
|---|---|
| `ARCH.md` | file map, the two-plane index argument, RLS predicate |
| `PROFILE.md` | how I'm taught, tracked gaps, style signals |
| `log/module-01.md` | M1 decision, its bug, five open carry-forwards |
| `log/module-02.md` | M2 decision, RLS drift diagnosis, fix, open threads |
| `log/module-03.md` | M3 decision, the open-redirect BREAK, the API design rule |
| `log/module-04.md` | M4 API-key auth, the api_keys privilege excess, rate-limit matrix |
| `log/module-05.md` | M5 PII-in-logs BREAK/FIX, the second pre-existing leak via `exc_info`, `check_log_pii.py` |
| `log/module-06.md` | M6 cache-aside, the cache-vs-RLS argument, invalidate-after-commit, the herd BREAK/FIX, two controls |
| `log/module-07.md` | M7 idempotent-before-at-least-once, migration 0010 analytics RLS, retention purge by partition drop, the unbounded-retry BREAK/FIX |
| `../upsk-system-design-workspace/artifacts/tickets/module-05/` | 6 SkillSwap tickets + `PROJECT-STANDARDS.md` (the cross-cutting doc that anti-scope lines now point at) |
| `../upsk-system-design-workspace/artifacts/slices-module-04.md` | 5 vertical slices + Slice 1.5; the representation-vs-feature rule |
| `../upsk-system-design-workspace/artifacts/contracts/module-06-interface-contracts.md` | shared types pinned to *rendering*; the table-based sync point; migration ownership |
| `../upsk-system-design-workspace/progress/ai-augmented-engineering/module-04/` | the 17-finding review + the fix prompt; the two criticals were runtime failures |
| `../upsk-system-design-workspace/progress/ai-augmented-engineering/module-05/iteration-log.md` | the restart call, and surface quality *falling* 4→3 as the correct outcome |
| `api/scripts/check_redos.py` | backtracking budget + per-shape SIGALRM. **Watched failing**: exits 1 on a reintroduced vulnerable regex. Known weakness: growth ratios are noise-dominated at microsecond timings and may flap in CI. |
| `../upsk-system-design-workspace/postmortem.md`, `postmortem-rewrite.md` | TC M04 — blameless incident analysis, the blame audit |
| `FULL-BRIEF.md` | all of the above unsplit — only for a cold handoff to another tool |
