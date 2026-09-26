# Deploying LinkOps

One image, two process types, against managed Postgres and Redis
(`decisions.module_10.deploy_strategy = multi_service`). This document is the
part of a deploy that is not code: which variables must be set, when migrations
run, and what to do when a deploy goes wrong.

## Services

| service | command | scales with |
|---|---|---|
| `web` | `uvicorn app.main:app --host 0.0.0.0 --port ${PORT}` | request volume |
| `worker` | `python worker/main.py` | analytics queue depth |

Same image (`ghcr.io/<repo>:<sha>`), different command. They must be separate
services because their load profiles are opposite: the web process is
latency-critical, the worker is CPU-heavy on the rollup, and co-locating them
lets a backlog steal the redirect's latency budget — the separation Module 07
was built around. Postgres and Redis are **managed services**, not containers
this repo runs: a stateful store sharing the app's deploy lifecycle would lose
the queue and the database on every release.

## Environment variables

Required — the process refuses to boot if any is missing, malformed, or (for the
URLs) empty. That is `app/config.py` doing its job; a missing value is a loud
failure at startup, never a silent default.

| variable | example | notes |
|---|---|---|
| `PORT` | `8000` | injected by the platform; `1024–65535`, no default |
| `ENVIRONMENT` | `production` | `development` \| `staging` \| `production` |
| `DATABASE_URL` | `postgresql+psycopg://app:…@host/db` | app role, RLS applies |
| `MIGRATION_DATABASE_URL` | `postgresql+psycopg://owner:…@host/db` | owner role, DDL |
| `REDIS_URL` | `redis://host:6379` | cache + analytics queue |
| `PUBLIC_BASE_URL` | `https://lnk.example` | origin for `short_url`; sets the CORS origin outside development |
| `CLICK_IP_SALT` | *(a real secret)* | **fatal if empty outside development** — an unsalted IP hash is reversible |

Optional, with safe defaults:

| variable | default | notes |
|---|---|---|
| `LOG_LEVEL` | `info` | runtime-changeable, no redeploy needed |
| `CLICK_RETENTION_DAYS` | `30` | raw click retention; the rollup is kept regardless |

Deliberately **not** environment variables: the CORS origin (derived from
`PUBLIC_BASE_URL` + `ENVIRONMENT`, so it cannot be forgotten into a permissive
state) and `HOST` (the container CMD binds `0.0.0.0`; only local dev uses
loopback).

## Health and readiness

- `GET /health` — liveness, dependency-free. The platform's restart check and
  the Docker `HEALTHCHECK` poll this. It must stay dependency-free so a database
  blip does not restart every container at once.
- `GET /ready` — readiness, checks Postgres only. Failure pulls one instance out
  of rotation without killing it. **Redis is deliberately excluded**: it is a
  cache with a database fallback, so a Redis outage must degrade latency, not
  drop the fleet out of rotation.

## Migrations — a one-off release command, not on startup

Run `alembic upgrade head` (as `MIGRATION_DATABASE_URL`, the owner role) **once
per release, before the new version takes traffic** — Railway's *release
command* or an equivalent one-off job. Not on process startup, for two reasons
this architecture makes concrete:

1. **Two process types would race.** `web` and `worker` both booting would each
   try to migrate; a release command runs exactly once.
2. **Boot must not depend on a schema change succeeding.** A failed startup
   migration is a service that will not start; a failed release command is a
   release that does not proceed, leaving the previous version running.

Migrations are written **expand/contract** so a rollback is safe (below): add a
column or a policy before the code needs it, remove the old thing only after the
code that used it is gone. This is why migration 0010 *added* an analytics RLS
policy rather than renaming a column — the app before and after both run against
the intermediate schema.

## Rollback

**Application:** redeploy the previous image tag to both `web` and `worker`.
Images are tagged by commit SHA, never `:latest`, so "the previous version" is
an exact, addressable artifact. Because migrations are expand/contract, the
previous app runs against the current schema — no schema rollback is needed for
an app rollback, which is the whole point of the discipline.

**Migration:** every migration has a tested `downgrade()`. But prefer *rolling
forward* — a new migration that corrects the problem — over `downgrade` in
production, because a down-migration that drops a column destroys data the
rolled-back app may still be writing. Reserve `downgrade` for a migration that
has not yet taken traffic.

**Shutdown safety:** on `SIGTERM` the web process flips `/ready` to false
*before* uvicorn stops accepting, so the platform stops routing to a closing
server instead of losing the in-flight window. The container runs uvicorn as
PID 1 (`exec` form) so the signal actually reaches it.

## Timeouts

Bounded everywhere a wait could otherwise hang behind a wedged dependency:

- **DB connect** 5s and **pool checkout** 5s (`app/db.py`) — a network black
  hole or an exhausted pool fails fast with a 503 the load balancer can act on,
  instead of holding a worker thread for the OS default ~2 minutes.
- **`/ready` probe** a 2s `statement_timeout` — an unbounded readiness query
  against a hung database would make readiness itself hang.
- `statement_timeout` is set per transaction, not globally: a redirect and a
  background rollup have different right answers for "too long".
