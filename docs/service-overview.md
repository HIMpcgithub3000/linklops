# LinkOps — Operations Manual

*You have been handed the pager. This is the service you now operate. This is not
the developer README (`../README.md`) — it is what you need at 3 AM.*

## Purpose

LinkOps is a multi-tenant URL shortener: it creates short links, redirects the
public at them, and counts clicks. Tenant isolation is enforced by the database
(row-level security), not by application code.

## Dependencies

| Dependency | Type | What happens without it | Fallback (Module 06) |
|---|---|---|---|
| **PostgreSQL** | primary datastore | management writes fail (503); redirects survive briefly on warm cache | fail-closed on writes; `/ready` pulls the instance out of rotation |
| **Redis** | cache + analytics queue | redirects still work (serve from DB); click counts pause | **fail-open**: cache miss==outage; a circuit breaker trips after 5 failed enqueues and drops clicks at zero cost |
| **Analytics worker** | separate process | click counts stall; redirects unaffected | jobs park on a durable processing list; orphan-recovery on restart |
| External APIs | — | none exist | LinkOps calls no third party |

## Endpoints

- `GET /health` — liveness (process alive; dependency-free — never checks a DB).
- `GET /ready` — readiness (checks **Postgres only**; deliberately not Redis).
- `GET /metrics` — Prometheus scrape (counter + duration histogram, route-templated).
- `GET /r/{code}` — the public redirect (302). The hot path.
- `POST /links`, `GET /links`, `GET /links/search`, `GET /links/{id}`,
  `PATCH /links/{id}`, `GET /links/{id}/analytics` — the tenant-scoped management
  plane (all require `X-API-Key`).

## Configuration (`../api/.env.example`)

| Variable | Controls | Safe at runtime? |
|---|---|---|
| `LOG_LEVEL` | logging verbosity | **yes** — read from config, no redeploy needed |
| `CLICK_RETENTION_DAYS` | raw-click retention (purge) | yes (affects the next purge run) |
| `DATABASE_URL` / `REDIS_URL` / `PUBLIC_BASE_URL` / `PORT` / `ENVIRONMENT` | core wiring | **no — requires restart** |
| `CLICK_IP_SALT` | IP-hash salt; **fatal if empty outside development** | no — restart |

Timeouts (connect 5s, pool 5s, statement 2s) and the breaker thresholds
(`fail_max=5`, `reset_timeout=30s`) are code constants in `app/db.py`,
`app/breaker.py` — changing them is a deploy, not a runtime toggle.

## Deploy (exact)

```
# Deploy is automatic on push to main (CI builds a SHA-tagged image).
git push origin main
# Migrations run as a one-off release command (NOT on startup):
alembic upgrade head          # as MIGRATION_DATABASE_URL, before traffic
# Verify:
curl -s https://<host>/health   # {"ok": true}
curl -s https://<host>/ready    # {"ok": true}
```

## Rollback (exact)

```
# Redeploy the previous SHA-tagged image (images are never :latest):
#   find the previous good SHA from the registry / CI history, then redeploy it
#   to BOTH the web and worker services.
# Migrations are expand/contract, so the previous app runs against the current
# schema — no schema rollback needed for an app rollback.
# Prefer rolling FORWARD (a corrective migration) over `alembic downgrade` in
# prod: a down-migration that drops a column destroys data the rolled-back app
# may still write.
curl -s https://<host>/ready    # confirm {"ok": true} after redeploy
```

## Ownership

- **Owner:** Platform team.
- **Channel:** `#linkops-oncall`.
- **Escalation:** primary on-call → secondary on-call → engineering manager.
- **No response in 15 minutes:** page the manager and open an incident; database
  issues route to `#data-eng`, ingress/registry to `#platform-infra`.
