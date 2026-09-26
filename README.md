# LinkOps

A multi-tenant URL shortener. Create short links, redirect the public at them,
count clicks, and search — with tenant isolation enforced by the database, not by
hope. FastAPI + PostgreSQL + Redis, one image, two process types (API + analytics
worker).

**If you follow the steps below in order and end up with a broken system, the
README is wrong — file it, don't work around it.** (Last verified: 2026-08-18.)

## What's here

| Path | What it is |
|---|---|
| `api/app/` | the FastAPI service — routers, services, RLS binding, config contract |
| `worker/main.py` | the analytics worker (drains the click queue, rolls counts up) |
| `api/scripts/check_*.py` | 10 standing controls — each asserts one invariant, each watched failing |
| `api/tests/` | pytest suite (integration-heavy, real Postgres) |
| `api/alembic/` | migrations (`0001`…`0012`) |
| `.github/workflows/ci.yml` | lint + 10 controls + pytest against Postgres & Redis |
| `DEPLOY.md` | env vars, migration-as-release-command, rollback plan |
| `../upsk-session/log/module-*.md` | the decision records — *why* each piece is shaped as it is |

## Prerequisites

- **Docker** — `docker --version` (any recent version). Postgres and Redis run in
  containers; you do not install them on the host.
- **Python 3.14** — `python3 --version`. If missing: `brew install python@3.14`
  (macOS) or your distro's package.

## Run it locally

1. **Start the datastores.** Postgres on `:5433`, Redis on `:6379`:
   ```
   docker start linkops-postgres linkops-redis
   ```
   First time only (if the containers don't exist yet):
   ```
   docker run -d --name linkops-postgres -p 5433:5432 \
     -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=linkops postgres:16-alpine
   docker run -d --name linkops-redis -p 6379:6379 redis:7-alpine
   ```

2. **Create the virtualenv and install deps:**
   ```
   cd api
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
   ```

3. **Configure.** Copy the contract file and fill real values — the app *refuses
   to boot* on a missing or malformed key, so this is not optional:
   ```
   cp .env.example .env
   ```
   `.env` needs at minimum `DATABASE_URL`, `MIGRATION_DATABASE_URL`, `REDIS_URL`,
   `PUBLIC_BASE_URL`, `PORT`, `ENVIRONMENT=development`. See `DEPLOY.md` for the
   full table. Outside development, `CLICK_IP_SALT` is also required (an unsalted
   IP hash is reversible).

4. **Run migrations** (as the owner role):
   ```
   .venv/bin/alembic upgrade head
   ```

5. **Start the API** and, in a second terminal, the worker:
   ```
   .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   .venv/bin/python ../worker/main.py
   ```

6. **Verify it's alive:**
   ```
   curl -s localhost:8000/health          # {"ok": true}
   curl -s localhost:8000/ready           # {"ok": true}  (checks Postgres)
   ```

## Test it

Tests run against a **dedicated** database (`linkops_test`) — a safety guard
refuses to run against anything not named like a test DB, so a mis-set URL fails
loudly instead of mutating real data.

```
# one-time: create + migrate the test database
docker exec linkops-postgres psql -U postgres -c "CREATE DATABASE linkops_test"
DATABASE_URL=postgresql+psycopg://upsk_app:<pw>@127.0.0.1:5433/linkops_test \
MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5433/linkops_test \
  .venv/bin/alembic upgrade head

# run the suite (point DATABASE_URL at linkops_test)
DATABASE_URL=.../linkops_test MIGRATION_DATABASE_URL=.../linkops_test .venv/bin/pytest -q
```

Run the standing controls (against the dev database, which has real data):
```
for c in api/scripts/check_*.py; do .venv/bin/python "$c"; done
```
All 10 exit 0. Each was watched failing — reintroduce the defect it guards and it
goes red. That is the difference between a control and a comment.

## Make a change and ship it

1. Branch, edit, add/adjust a test **and** the control that guards the invariant.
2. `pytest -q` green, `ruff check .` clean, controls green.
3. Push. CI runs lint + migrations + 10 controls + pytest against real Postgres &
   Redis and builds a **SHA-tagged** image (never `:latest`).
4. Deploy and roll back per **`DEPLOY.md`** — migrations are a one-off release
   command (expand/contract), rollback is redeploying the previous SHA image.

## The one thing that is *not* shipped

The teams/invitations feature (`app/routers/teams.py`) is **intentionally not
wired in**: it imports a `Principal` symbol that `app/auth.py` does not define, so
it cannot be imported, and `main.py` does not include it. Its data tables now have
row-level security (migration 0012), but the router needs `Principal` defined, an
`is-admin-or-owner` gate, and cross-tenant tests before it ships. Tracked as
finding F1 in `system-review-ai-generated.md`. Do not wire it in without those.

## Where the *why* lives

Decisions aren't in this README — they're next to the code (docstrings,
migration comments) and in `../upsk-session/log/module-*.md`, one per module.
Read those before changing RLS, the cache, or the analytics idempotency: each
explains a defect that shaped the design, and undoing it reintroduces the defect.

## Who to ask

Service owner: this repo's maintainers. The controls and `DEPLOY.md` answer most
"how does X work / how do I ship" questions before a human has to.
