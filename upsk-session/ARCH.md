# Architecture — file map and the arguments that hold it up

## File map

```
api/
  app/config.py    pydantic-settings Settings, fail-fast validation, three guards,
                   key-parity check, credential redaction on the error path
  app/db.py        engine, sessionmaker, request-scoped tenant binding (sets the
                   app.tenant_id GUC per request). AUTH_BACKEND = "dev-header",
                   X-Dev-Tenant-Id, with a startup guard that refuses to boot when
                   ENVIRONMENT != development. Module 04 replaces it with a JWT claim.
  app/models.py    SQLAlchemy 2.0. Link, ClickEvent. UUIDv7 time-ordered PKs — not
                   uuid4, whose random keys scatter B-tree inserts and cause page
                   splits and write amplification on every insert.
  app/main.py      GET /health (dependency-free), GET /ready (hard deps only,
                   explicit timeout, 503 so the instance deregisters, not killed)
  alembic/versions/0001_initial_schema.py
                   0002_rls_empty_guc.py
                   0003_restore_with_check.py
  scripts/config_drill.sh          M1 config failure drill
  scripts/module-02-seed-query.py  seeds two tenants, runs four negative controls,
                                   writes progress/evidence/module-02/query-by-code.txt
  scripts/check_rls.py             asserts live RLS matches the migrations, exit 0/1
  .env / .env.example              PORT, ENVIRONMENT, DATABASE_URL,
                                   MIGRATION_DATABASE_URL, MONGODB_URI, REDIS_URL,
                                   JWT_SECRET, API_KEY_A, API_KEY_B
progress/state.json                decisions, per-module progress, carry-forwards
progress/evidence/module-02/query-by-code.txt
```

`progress/state.json` says `next_step: "context"` for module 2 — stale. The
authoritative step is whatever `upsk status` reports.

## The two-plane argument (load-bearing, reuse the framing)

`tenant_id` plays opposite roles in the two access patterns:

```
data plane (redirect)      WHERE code = ?        tenant_id is an OUTPUT
                           public, unauthenticated, carries no tenant context,
                           so `code` must be globally unique — the lookup
                           resolves the owner rather than filtering by it

management plane (CRUD)    WHERE tenant_id = ?   tenant_id is an INPUT
                           supplied by the caller's identity, constrains what
                           may be seen
```

Same column, opposite direction — which is why they need different indexes rather than
one compromise index serving neither. `click_events` is RANGE partitioned by
`clicked_at`.

## RLS

Predicate on both `links` and `click_events`, `FORCE ROW LEVEL SECURITY`, applied to a
dedicated non-owning app role:

```sql
tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
```

`uq_links_code` does two jobs at once — uniqueness and redirect lookup speed. Drop the
uniqueness and keep a plain index and the redirect stays fast, but the data-plane
guarantee that a code resolves to exactly one owner is gone.
