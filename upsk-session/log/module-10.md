# Module 10 — CI/CD & Deployment

System Design Fundamentals, Operator pack. **Final module of the skill.**

## Decision

`decisions.module_10.deploy_strategy = multi_service` — *selected by the
instructor under Himanshu's standing direction.*

One image, two process types (`web`, `worker`), against **managed** Postgres and
Redis. Forced by earlier decisions: the worker and API are one artifact (Module
01 layout) but opposite load profiles — API latency-critical, worker CPU-heavy
on the rollup — so co-locating them lets a backlog steal the redirect's latency
budget (the Module 07 separation). Postgres and Redis are managed, not
containers this repo runs: a stateful store on the app's deploy lifecycle loses
the queue and the database on every release.

## What already existed (from earlier modules)

The deployment *mechanics* were mostly in place, so this module was mostly
verification plus closing the CI gap:

- Dockerfile: multi-stage, non-root, binds `0.0.0.0`, `--port ${PORT:-8000}`,
  HEALTHCHECK on `/health`, `exec` form so uvicorn is PID 1 and gets SIGTERM.
- `/health` (dependency-free) and `/ready` (Postgres only, Redis excluded).
- Graceful shutdown: SIGTERM flips `/ready` false before uvicorn stops
  accepting, chained onto uvicorn's own handler.
- Config contract: every required var is a refused boot if missing/malformed.

## Built / changed this module

**1. Explicit timeouts (`app/db.py`).** The module's failure mode is a hang, not
a crash. Added `pool_timeout=5` and `connect_args={"connect_timeout": 5}` — a
wedged database or exhausted pool now fails fast with a 503 the load balancer can
act on, instead of holding a worker thread for the OS default ~2 minutes.
`statement_timeout` stays per-transaction (a redirect and a rollup disagree on
"too long"), which was already true in `ping()`.

**2. CI now runs the real suite (`.github/workflows/ci.yml`).** This retires
carry-forwards accumulated since Module 05. The `verify` job now:

- adds a **Redis service** alongside Postgres;
- names the CI database **`linkops_test`**, so the Module 09 pytest guard passes
  by honest naming rather than an override — CI's ephemeral container genuinely
  is a test database;
- runs **eight controls** (was four): added `check_log_pii`, `check_cache_scoping`,
  `check_cache_behaviour`, `check_analytics_pipeline`;
- runs **`pytest`** as its own step, sharing one env block via a YAML anchor so
  the controls and the tests cannot drift on the connection URLs.

`check_tenant_coverage` is deliberately **excluded** from CI: it exits 1 on a
real, documented gap (`teams`/`team_memberships`/`invitations` have no RLS), and
a control known to fail must not gate the pipeline — that trains everyone to
ignore a red build. It stays a by-hand control until the gap is closed.

**3. `pytest>=8` added to `requirements-dev.txt`.** It was missing — CI's Tests
step would have failed with "pytest: not found". No httpx/TestClient: the suite
drives service functions against a real bound session, so a network client would
add a dependency and prove nothing.

**4. `DEPLOY.md`** — the non-code half of a deploy:

- the required/optional env var tables (and what is deliberately *not* a var:
  the CORS origin, derived so it cannot be forgotten into permissive; `HOST`);
- **migrations as a one-off release command, not on startup** — two process
  types would race, and boot must not depend on a schema change succeeding;
- **rollback**: redeploy the previous SHA-tagged image; migrations are
  expand/contract so the previous app runs against the current schema and no
  schema rollback is needed. Prefer rolling forward over `downgrade` in prod.

## Evidence

```
8 controls exit 0            (was 4 in CI)
pytest                       32 passed against linkops_test
ruff                         clean across app scripts tests worker
ci.yml                       valid YAML; services postgres+redis;
                             steps: install, lint, migrate, controls, tests
app + worker import          ok with the new engine timeouts
```

CI YAML was validated by parsing; it has not run on a GitHub runner (no remote
configured), so the workflow logic is verified and the platform integration is
not — the same honest split noted since the pipeline was first written.

## Skill complete

System Design Fundamentals: 10/10 modules. The other threads that remain are
cross-cutting and tracked in STATE.md — the `check_tenant_coverage` RLS gap on
the teams tables is the one with teeth.

## Carry-forward

- **The teams-tables RLS gap** (`teams`, `team_memberships`, `invitations`) is
  now the last un-closed control. It has been carried since AI-Aug M04. Closing
  it is a migration adding a `tenant_isolation` policy to each — straightforward,
  but real work that touches the invitation flow, so it was not done blind here.
- CI has never executed on a runner. Configuring a remote and watching the first
  real run is the only way to close the "logic tested, integration not" split.
- The `run_tests.sh` DB-name-swap helper still lives in the session scratchpad,
  not the repo. CI does not need it (it sets the URLs directly); a local
  developer does. Worth a `Makefile` target.

## BREAK / FIX — readiness over-reports a cache outage

**Injected.** Added `redis_client.client.ping()` to `/ready`, justified as being
thorough about the whole dependency graph — the exact change the endpoint's
docstring says must not be made.

**Symptom, reproduced live.** Redis up: `/ready` 200. Redis stopped:

```
/ready   200 -> 503        (platform pulls every pod out of rotation)
/health  200               (liveness fine, no restart)
redirect 302 -> target     (the service is serving)
```

A cache outage the service survives, reported as unreadiness on every pod at
once — a total outage manufactured from a partial one. Health and the redirect
both proved the service was fine; only the over-thorough readiness check
disagreed.

**Fixed** by restoring `/ready` to Postgres-only, and proven live both ways:

```
fix, redis down:   /ready 200,  redirect 302    (cache outage no longer unreadiness)
graceful shutdown: /ready 200 -> 503 at +100ms, server stops accepting at +200ms
```

The shutdown ordering is the point: SIGTERM flips `/ready` false *before* uvicorn
stops accepting, so the platform stops routing into a closing server.

## Standing control (ninth)

`api/scripts/check_readiness_contract.py` — guards a decision, not a computation,
which is why it needs two detectors:

- **behaviour** — drives the real handlers with a broken database and a broken
  Redis swapped in; asserts `/ready` fails on Postgres, ignores Redis, and
  `/health` ignores both.
- **source** — scans `ready()`'s code (docstring stripped by AST, or the
  documentation of *why Redis is excluded* would read as the bug) for any Redis
  call, and `health()` for any dependency call.

**Watched failing:** reintroducing the Redis ping flips both detectors —
`/ready returned 503 with only Redis down` and `ready() calls into Redis`.

Added to CI (ninth control in the `verify` job).

## Final state

9 controls exit 0 · pytest 32 passed · ruff clean · readiness and graceful
shutdown proven live · CI runs lint + 9 controls + pytest against
postgres+redis services.
