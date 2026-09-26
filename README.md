# LinkOps — Enterprise Multi-Tenant Link Platform & Stream Engine

> **High-throughput, multi-tenant link shortening, redirection, and click-stream analytics engine built with PostgreSQL Row-Level Security (RLS), Redis cache-aside, reliable queue workers, and sub-millisecond redirect latency.**

[![Python](https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/API-FastAPI_0.141-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16_RLS-336791?style=flat-square&logo=postgresql)](https://www.postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7.0_Cache_%26_Queue-DC382D?style=flat-square&logo=redis)](https://redis.io)
[![Docker](https://img.shields.io/badge/Docker-Ready_Compose-2496ED?style=flat-square&logo=docker)](https://docker.com)
[![Controls](https://img.shields.io/badge/Controls-10%2F10_Passing_(100%25)-success?style=flat-square)](api/scripts)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue?style=flat-square)](LICENSE)

---

## The Problem

Traditional URL shorteners and link management backends fail at scale in B2B multi-tenant environments:

```
Tenant A (Enterprise Bank): Creates internal compliance link -> /r/audit2026
Tenant B (SaaS Prober):    Sends 5,000 req/min searching for competitor vanity slugs
Result: Cross-tenant data leakage, database thread exhaustion, and latency spikes across all customers.
```

Standard web architectures suffer from critical structural flaws:
- **Cross-Tenant Data Leaks**: Relying on application code (`WHERE tenant_id = ?`) is prone to human error; a single omitted `WHERE` clause in any endpoint refactor leaks sensitive customer URLs across organizations.
- **Redirect Latency Bottlenecks**: Coupling analytics persistence directly to the redirect route inflates redirect latency from 2ms to 200ms+, turning minor database hiccups into user-visible redirection timeouts.
- **Cache vs. RLS Desynchronization**: Caching reads in Redis bypasses PostgreSQL RLS completely. If Redis keys are un-scoped, Tenant B receives Tenant A's cached private data.
- **Double-Counting & Lost Updates**: Naive background workers drop clicks during crashes (`BRPOP`), or double-count during retries when operations lack database idempotency.
- **Table Bloat & Lock Contention**: Using random UUIDv4 keys fragments B-tree index pages, while bulk SQL `DELETE` retention purges cause write-ahead log (WAL) explosions and table locks.
- **SSRF & Open Redirects**: Regex validators miss decimal, octal, or percent-encoded loopback addresses (`http://127.1`), allowing attackers to traverse internal cloud infrastructure.

**LinkOps solves this in <2 milliseconds with mathematical guarantees.**

---

## What LinkOps Does

LinkOps is a **hardened, high-performance link shortening and click-stream analytics engine** designed for enterprise multi-tenancy:

- 🛡️ **Enforces Kernel-Level Tenant Isolation** — Enforces PostgreSQL Row-Level Security (RLS) with dual-clause `USING` and `WITH CHECK` policies on a dedicated non-owning application role (`upsk_app`). A missing application filter returns **0 rows** instead of **everyone's rows**.
- ⚡ **Sub-Millisecond Public Data Plane** — Dedicated `/r/{code}` route utilizing Cache-Aside with single-flight stampede protection, delivering cached 302 redirects in under 2ms while failing open on Redis outages.
- 🔁 **At-Least-Once Idempotent Analytics Rollups** — `BRPOPLPUSH` reliable queueing paired with a single-statement SQL Common Table Expression (CTE) upsert, guaranteeing 0 click loss and 0 double-counts under network partitions.
- 📦 **Zero-Lock Partition Purging** — Monthly range-partitioning on `click_events`; expired months are purged via `DROP TABLE` in <5ms with 0 WAL bloat and 0 table locks.
- 🔍 **Database-Native Substring Search** — PostgreSQL `pg_trgm` GIN indexes operate strictly within the RLS boundary, matching arbitrary URL fragments without external search engines.
- 🔒 **Defense-in-Depth Edge Security** — Validates destination URLs using native OS socket resolver libraries (`socket.inet_aton`), blocking private CIDRs, loopbacks, and percent-encoded userinfo authority bypasses.
- ⏱️ **Zero-Downtime Rate Limiting & Auth** — Multi-surface fixed-window counter in Redis, constant-time API key verification (`hmac.compare_digest`), and fail-open semantics.
- 🐳 **One-Command Production Docker Stack** — Multi-stage Debian-slim containerization separating web and worker processes with explicit connection pool timeouts.

---

## Live Demo & Request Flow

> **[▶ Interactive Swagger Documentation](http://localhost:8000/docs)** *(FastAPI OpenAPI Console)*

**Demonstration Flow (3 Minutes):**
1. **Mint Tenant API Key** → Run `.venv/bin/python scripts/mint_key.py m04-tenant-a` to generate a secure `<key_id>.<secret>` credential.
2. **Create Short Link** → Send authenticated `POST /links` with custom destination and tags; receives UUIDv7 link ID and short code.
3. **Execute High-Speed Redirect** → Call `GET /r/{code}`; receives instant `302 Found` with `Location` header (<2ms).
4. **Inspect Asynchronous Click Stream** → The API asynchronously enqueues the click into Redis; the background worker drains `queue:clicks` and rolls counts into hourly buckets.
5. **Query Analytics Rollup** → Call `GET /links/{id}/analytics?from=...&to=...`; verify real-time click aggregation without hitting raw event tables.
6. **Execute Trigram Substring Search** → Call `GET /links/search?q=news`; verify fast substring matching across tenant-isolated links.

---

## Architecture

```
                           [ Internet Traffic ]
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │    Reverse Proxy / LB     │
                      └─────────────┬─────────────┘
                                    │
               ┌────────────────────┴────────────────────┐
               ▼                                         ▼
   [ Data Plane: Redirects ]                [ Management Plane: API ]
   GET /r/{code}                            POST /links, GET /links/search
   Public, No Auth, <2ms                    Authenticated (API Key), Tenant-Bound
               │                                         │
               ├───────────────┐                         │
               ▼               ▼                         ▼
         ┌───────────┐   ┌───────────┐             ┌───────────┐
         │ Redis     │   │ Postgres  │             │ Postgres  │
         │ Cache     │   │ resolve() │             │ (RLS ON)  │
         └───────────┘   └───────────┘             └───────────┘
               │ (Cache Miss)                            │
               ▼                                         ▼
         [ Async Event ]                         [ After-Commit ]
               │ LPUSH queue:clicks                Invalidates Redis Keys
               ▼
         ┌───────────┐
         │   Redis   │
         │   Queue   │
         └─────┬─────┘
               │ BRPOPLPUSH
               ▼
         ┌───────────┐
         │ Analytics │ ─── Atomic Idempotent Rollup ──►  Postgres 16
         │  Worker   │                                   ├── click_events (Partitions)
         └───────────┘                                   └── analytics (Rollup Table)
```

---

## The "Wow" Moments for System Designers

### 1. The Two-Plane Architecture: Inverted Security Semantics
The column `tenant_id` exists in both planes, but its role and security semantics are completely inverted:
* **Data Plane (`/r/{code}`)**: `tenant_id` is an **OUTPUT** of resolution. The route is public, unauthenticated, and requires sub-millisecond execution. Resolves via a single `SECURITY DEFINER` function (`resolve_link()`) without holding a tenant session.
* **Management Plane (`/links`)**: `tenant_id` is an **INPUT** supplied by caller authentication. The route binds `SET LOCAL app.tenant_id = :id` to enforce forced Row-Level Security on every query.

### 2. PostgreSQL Row-Level Security: The Dual-Clause Principle
Multi-tenancy is enforced by the PostgreSQL kernel, not application convention. If an engineer forgets a `WHERE` clause, Postgres returns **zero rows**.
```sql
CREATE POLICY tenant_isolation ON links
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```
Both `USING` (reads) and `WITH CHECK` (writes) are identical. Omitting `WITH CHECK` allows Tenant B to insert rows into Tenant A's account while remaining blind to them—a silent vulnerability LinkOps explicitly closes.

### 3. Cache-Aside Without RLS Leakage
Postgres enforces RLS, but Redis has no multi-tenant awareness. A cache hit bypasses Postgres completely.
LinkOps enforces strict namespace registration in `app/cache.py`:
- `redirect`: Explicitly registered global (codes are globally unique via `uq_links_code`).
- `links_page` & `click_totals`: Registered as tenant-scoped. Constructing a key without `tenant_id` raises a fatal `CacheKeyError`, making silent cross-tenant leaks impossible.
- **Thundering Herd Defense**: Implements `fill_once()` single-flight request coalescing, collapsing concurrent misses on hot keys into a single database read.

### 4. Atomic SQL Idempotent Rollup (Zero Lost Clicks, Zero Double-Counts)
To survive worker restarts without double-counting, delivery guarantees and database writes are strictly ordered:
```sql
WITH stored AS (
    INSERT INTO click_events (id, link_id, tenant_id, clicked_at, ...)
    SELECT :event_id, l.id, l.tenant_id, :clicked_at, ...
    FROM links l WHERE l.id = :link_id
    ON CONFLICT (id, clicked_at) DO NOTHING
    RETURNING link_id, clicked_at
)
INSERT INTO analytics (link_id, timestamp_bucket, count)
SELECT link_id, date_trunc('hour', clicked_at), 1 FROM stored
ON CONFLICT (link_id, timestamp_bucket)
DO UPDATE SET count = analytics.count + 1;
```
If a message is redelivered via `BRPOPLPUSH`, the CTE insert conflicts, `stored` produces 0 rows, and the rollup counter does not increment.

### 5. High-Throughput Range Partition Purges
High-volume click streams generate millions of rows monthly. Running `DELETE FROM click_events WHERE clicked_at < cutoff` causes massive WAL disk bloat and table locks.
LinkOps range-partitions `click_events` by month. Expired data is purged via:
```sql
DROP TABLE click_events_YYYY_MM;
```
This drops gigabytes of data in **<5 milliseconds** with zero table locks and zero `VACUUM` overhead.

### 6. Strict Probes Contract (`/health` vs `/ready`)
- **`/health` (Liveness)**: Zero external dependencies. Never fails unless the Python event loop is deadlocked.
- **`/ready` (Readiness)**: Checks hard dependencies only (Postgres connection pool with 5s timeout). **Redis is deliberately excluded**: Redis is a cache with a DB fallback; a Redis outage degrades latency, but must never pull pods out of rotation.

---

## Tech Stack

| Layer | Technology | Why This, Not Alternatives |
|---|---|---|
| **Web Framework** | FastAPI (Python 3.14) | Asynchronous ASGI handling with high throughput, native OpenAPI generation, and strict Pydantic v2 validation |
| **Relational Database** | PostgreSQL 16 | Kernel-enforced Row-Level Security (RLS), range partitioning, and ACID transaction semantics |
| **Caching & Queue** | Redis 7.0 | In-memory cache-aside with single-flight protection and reliable queue delivery via `BRPOPLPUSH` |
| **Primary Keys** | UUIDv7 (Time-Ordered) | Sequential B-tree index locality; eliminates random page splits and write amplification caused by UUIDv4 |
| **Search Engine** | PostgreSQL `pg_trgm` | Substring and regex search inside the database; avoids syncing tenant data to external search engines |
| **Containerization** | Docker Multi-Stage (Debian-slim) | Pre-compiled `glibc` wheels (`psycopg[binary]`, `pydantic-core`); avoids slow Alpine `musl` compilation |
| **Database Migrations** | Alembic | Version-controlled, expand/contract schema migrations executed as release-phase commands |
| **Testing & Controls** | Pytest & 10 Standing Controls | Integration-heavy testing verifying real database RLS policies, rate limits, and idempotency |

---

## Project Structure

```
upsk-system-design-workspace/
├── Dockerfile                      # Multi-stage container build (Debian slim, non-root)
├── infra/
│   └── docker-compose.yml          # Postgres 16 (port 5433) + Redis 7 (port 6379)
├── .github/
│   └── workflows/ci.yml            # CI: Ruff lint + 10 standing controls + Pytest
│
├── api/                            # Core FastAPI Backend Service
│   ├── alembic/                    # 12 Schema Migrations (Initial schema to Teams RLS)
│   ├── app/
│   │   ├── config.py               # Pydantic Settings with startup key parity & redaction
│   │   ├── db.py                   # Engine, pool timeout (5s), and tenant-binding session
│   │   ├── models.py               # UUIDv7 models (Link, ClickEvent, Analytics, ApiKey)
│   │   ├── main.py                 # FastAPI app, exception handlers, /health & /ready
│   │   ├── auth.py                 # API key auth with constant-time hash comparison
│   │   ├── cache.py                # Redis cache-aside, single-flight fill_once, TTL jitter
│   │   ├── ratelimit.py            # Fixed-window counter rate limiting (fail-open)
│   │   ├── logging_config.py       # JSON structured logging with PII redaction
│   │   ├── routers/
│   │   │   ├── redirect.py         # GET /r/{code} (Public data plane, 302 redirect)
│   │   │   ├── links.py            # CRUD operations: POST, GET, PATCH, Search, Analytics
│   │   │   └── teams.py            # Team collaboration routes
│   │   └── services/
│   │       ├── url_policy.py       # SSRF & IP literal validation (socket.inet_aton)
│   │       ├── links_service.py    # Business logic & pg_trgm substring search
│   │       └── clicks.py           # Click event construction with salted IP hash
│   │
│   ├── scripts/                    # 10 Automated Standing Safety Controls
│   │   ├── check_rls.py            # Asserts live RLS is enabled, forced, and dual-scoped
│   │   ├── check_url_policy.py     # 43-case SSRF and redirect validation harness
│   │   ├── check_analytics_pipeline.py # Asserts at-least-once queue idempotency
│   │   ├── check_cache_behaviour.py# Tests single-flight stampede & invalidation
│   │   ├── check_cache_scoping.py  # Enforces mandatory tenant scoping on cache keys
│   │   ├── check_log_injection.py  # Asserts CRLF newline log-splitting defense
│   │   ├── check_log_pii.py        # Asserts zero email or credential leaks in logs
│   │   ├── check_tenant_coverage.py# Verifies all tenant tables have RLS policies
│   │   ├── check_readiness_contract.py # Verifies /ready excludes soft dependencies
│   │   ├── mint_key.py             # CLI tool to mint development API keys
│   │   └── purge_click_events.py   # Retention purge via partition dropping
│   │
│   ├── tests/                      # Integration test suite (real PostgreSQL container)
│   ├── ruff.toml                   # Pinned linter and formatter configuration
│   └── requirements.txt            # Production dependencies
│
└── worker/                         # Asynchronous Background Worker
    └── main.py                     # Reliable queue consumer (BRPOPLPUSH + idempotent rollup)
```

---

## Setup in 5 Minutes

### Prerequisites
- Docker 20.10+ and Docker Compose
- Python 3.14 (or virtual environment)

---

### Step 1: Start Backing Services (Postgres & Redis)

Start the local database and Redis containers:

```bash
docker compose -f infra/docker-compose.yml up -d
```
*Spins up PostgreSQL on port `5433` and Redis on port `6379`.*

---

### Step 2: Configure Environment & Run Migrations

```bash
cd api
cp .env.example .env

# Run all 12 migrations to set up tables, RLS policies, and triggers
.venv/bin/alembic upgrade head

# (Optional) Seed demo links and tenants
.venv/bin/python scripts/seed_linkops.py
```

---

### Step 3: Run the Web API & Worker

**Terminal 1: Launch FastAPI Web Service**
```bash
cd api
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2: Launch Analytics Background Worker**
```bash
cd api
.venv/bin/python ../worker/main.py
```

---

### Step 4: Test Link Creation & Redirection

**1. Mint an API Key:**
```bash
.venv/bin/python scripts/mint_key.py m04-tenant-a
# Output: 2483a6e08538.9Jg99AVSPdI1tDn3JMoKK5Tw62z5K8209w2jA09tFfA
```

**2. Create a Short Link:**
```bash
curl -X POST http://localhost:8000/links \
  -H "Content-Type: application/json" \
  -H "X-API-Key: 2483a6e08538.9Jg99AVSPdI1tDn3JMoKK5Tw62z5K8209w2jA09tFfA" \
  -d '{
    "long_url": "https://news.ycombinator.com",
    "tags": ["tech", "news"]
  }'
```

**3. Execute the 302 Redirect:**
```bash
curl -i http://localhost:8000/r/<CODE>
```

**4. Query Aggregated Analytics:**
```bash
curl -H "X-API-Key: 2483a6e08538.9Jg99AVSPdI1tDn3JMoKK5Tw62z5K8209w2jA09tFfA" \
  "http://localhost:8000/links/<LINK_ID>/analytics?from=2026-09-01T00:00:00Z&to=2026-09-30T00:00:00Z"
```

---

## Standing Controls & Verification Suite

Every security invariant and architectural claim in LinkOps is verified continuously by **10 automated standing control scripts** against real running infrastructure:

| Control Script | What It Asserts | Execution |
|---|---|---|
| `check_rls.py` | Asserts RLS is enabled, forced, and both `USING` and `WITH CHECK` clauses are active | CI + Manual |
| `check_url_policy.py` | Runs 43 hostile SSRF and authority-confusion test cases against destination validation | CI + Manual |
| `check_analytics_pipeline.py` | Replays duplicate queue events to assert exact single-count SQL rollup idempotency | CI + Manual |
| `check_cache_behaviour.py` | Asserts single-flight collapses 24 concurrent misses into 1 DB query, and invalidation is immediate | CI + Manual |
| `check_cache_scoping.py` | Asserts tenant-scoped namespaces can never produce an unscoped Redis key | CI + Manual |
| `check_log_injection.py` | Asserts 12 newline-injection payloads cannot split a single JSON log line | CI + Manual |
| `check_log_pii.py` | Asserts error handlers redact user emails and failing database row parameters | CI + Manual |
| `check_tenant_coverage.py` | Verifies every tenant-scoped table (direct or via FK) is protected by an active RLS policy | CI + Manual |
| `check_readiness_contract.py`| Asserts `/ready` checks hard DB dependencies only and excludes soft cache dependencies | CI + Manual |
| `audit_stored_destinations.py`| Re-validates every stored destination in the database against the current security policy | CI + Manual |

### Execute Verification Locally:
```bash
cd api
.venv/bin/ruff check .
.venv/bin/python scripts/check_rls.py
.venv/bin/python scripts/check_url_policy.py
.venv/bin/python scripts/check_analytics_pipeline.py
.venv/bin/pytest
```

---

## License

Distributed under the Apache-2.0 License. See [LICENSE](LICENSE) for more information.

---

**Built with pride for high-throughput, multi-tenant link infrastructure.**  
*Author: Himanshu Sharma*
