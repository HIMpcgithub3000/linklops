# LinkOps: The Complete Assessment, System Design & Interview Preparation Master Guide

> **Author**: Himanshu Sharma  
> **Track**: FastAPI / PostgreSQL / Redis / Multi-Tenant B2B Architecture  
> **Status**: All 6 Skills Complete (100%)  
> **Target Role**: Senior / Staff Backend & Distributed Systems Engineer  

---

## Table of Contents
1. [Prerequisites: Core Technical Topics to Master First](#1-prerequisites-core-technical-topics-to-master-first)
2. [System Architecture Overview: LinkOps](#2-system-architecture-overview-linkops)
3. [Skill 1: System Design Fundamentals (Modules 01–10)](#3-skill-1-system-design-fundamentals-modules-0110)
4. [Skill 2: Debugging & Incident Response (Modules 01–07)](#4-skill-2-debugging--incident-response-modules-0107)
5. [Skill 3: Technical Communication (Modules 01–06)](#5-skill-3-technical-communication-modules-0106)
6. [Skill 4: AI-Augmented Engineering (Modules 01–08)](#6-skill-4-ai-augmented-engineering-modules-0108)
7. [Skill 5: Decomposition & Execution Planning (Modules 01–08)](#7-skill-5-decomposition--execution-planning-modules-0108)
8. [Skill 6: Production Readiness (Modules 01–08)](#8-skill-6-production-readiness-modules-0108)
9. [Complete Codebase File Index & Component Guide](#9-complete-codebase-file-index--component-guide)
10. [End-to-End Runtime Execution & Data Flows](#10-end-to-end-runtime-execution--data-flows)
11. [Top 20 Interview Questions & Battle-Tested Answers](#11-top-20-interview-questions--battle-tested-answers)

---

## 1. Prerequisites: Core Technical Topics to Master First

Before reading the assessment decisions, make sure you understand these foundational concepts. Interviewers will test your grasp of these building blocks:

### 1. Multi-Tenancy Architecture Patterns
*   **Database-per-tenant**: Highest isolation, highest operational cost (running/migrating N databases).
*   **Schema-per-tenant**: Separate Postgres schemas in one DB; connection pooling and cross-tenant queries become complex.
*   **Shared Database, Shared Schema (Row-Level Isolation)**: All tenants share tables. Every query must be scoped by `tenant_id`.
    *   *The Trap*: Relying on application code (`WHERE tenant_id = ?`) is prone to human error. A single missing `WHERE` clause leaks data across tenants.
    *   *The Solution*: PostgreSQL **Row-Level Security (RLS)**. The database engine enforces the tenant filter on every query automatically.

### 2. PostgreSQL Row-Level Security (RLS) Mechanics
*   `ENABLE ROW LEVEL SECURITY`: Turns on RLS for the table.
*   `FORCE ROW LEVEL SECURITY`: Applies RLS even to table owners (except superusers).
*   **The Two Clauses**:
    *   `USING (condition)`: Filter for **READS** (`SELECT`, `UPDATE`, `DELETE`). Rows failing this return empty.
    *   `WITH CHECK (condition)`: Validation for **WRITES** (`INSERT`, `UPDATE`). Operations failing this abort with an error.
    *   *Critical Bug*: If `WITH CHECK` is omitted or set to `true`, Tenant B can insert records into Tenant A's account and never see them!
*   **Session GUC (Grand Unified Configuration)**: Setting a connection-local variable:
    ```sql
    SET LOCAL app.tenant_id = '123e4567-e89b-12d3-a456-426614174000';
    ```
    The policy checks: `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`.
*   `SECURITY DEFINER` vs `SECURITY INVOKER`:
    *   `SECURITY INVOKER` runs with the calling user's permissions (subject to RLS).
    *   `SECURITY DEFINER` runs with the function creator's privileges (can bypass RLS safely for tightly audited operations like public short-link resolution).

### 3. Primary Key & Indexing Strategies (UUIDv7 vs UUIDv4)
*   **UUIDv4**: Purely random 128-bit numbers.
    *   *Drawback*: Random inserts scatter across the B-tree index, causing constant disk page splits, low cache efficiency, and write amplification.
*   **UUIDv7**: Time-ordered 128-bit numbers (Unix epoch timestamp prefix + random bits).
    *   *Advantage*: Inserts append sequentially to the right edge of the B-tree, keeping index pages packed and cache-resident while maintaining global uniqueness.
*   **GIN Trigram Indexes (`pg_trgm`)**: Breaks strings into 3-character slices (e.g., `"apple"` -> `{"  a", " ap", "app", "ppl", "ple", "le "}`). Enables fast substring and regex search (`WHERE url ILIKE '%keyword%'`) without full table scans.

### 4. Distributed Caching (Redis) Mechanics
*   **Cache-Aside (Lazy Loading)**: The application checks the cache first. On miss, it reads the database, writes to cache, and returns. If Redis is down, the system continues serving from the database.
*   **Write-Through**: The application writes to cache and database simultaneously. If Redis fails, writes fail or become inconsistent.
*   **Thundering Herd / Cache Stampede**: When a popular key expires, hundreds of concurrent requests experience a cache miss simultaneously and hammer the database.
    *   *Mitigation*: **Single-Flight** (coalescing requests so only one query hits the DB while others wait) and **TTL Jitter** (randomizing TTL by ±10s to prevent synchronized expiration).
*   **Invalidation Races**: Invalidating cache *before* database commit allows concurrent reads to re-cache the old value. Invalidation must always happen **after** database commit (`after_commit` hook).

### 5. Asynchronous Messaging & Queue Delivery Guarantees
*   **At-Most-Once (`BRPOP`)**: The job is removed from the queue immediately upon receipt. If the worker crashes mid-processing, the job is permanently lost.
*   **At-Least-Once (`BRPOPLPUSH` / Redis Streams)**: The job is atomically copied to a "processing" queue before execution. If the worker dies, an orphan recovery job re-queues it.
*   **Idempotency**: An operation that can be executed multiple times without changing the result beyond the initial application.
    *   *Rule*: **Never implement at-least-once delivery without first ensuring the write is idempotent**, otherwise retries will double-count data.

### 6. Observability & Orchestration Probes
*   **Liveness (`/health`)**: Answers: *"Is the process stuck or deadlocked?"* Must have **zero dependencies** (no DB, no Redis). If this fails, the orchestrator terminates and restarts the container.
*   **Readiness (`/ready`)**: Answers: *"Can this instance accept customer traffic right now?"* Checks **hard dependencies only** (e.g. Postgres pool ping). If this fails, the load balancer removes the pod from traffic rotation without killing it.
*   *Why exclude Redis from `/ready`?* Redis is an optimization (cache) and async buffer (queue). If Redis dies, the service can still serve redirects directly from Postgres. Including Redis in `/ready` turns a cache hiccup into a 100% total outage.

### 7. Security: SSRF & URL Parsing Pitfalls
*   **Server-Side Request Forgery (SSRF)**: An attacker tricks a server into making HTTP requests to internal/private resources (e.g. `http://169.254.169.254/latest/meta-data` or `http://127.0.0.1:5432`).
*   **Parser Desynchronization**: A regex validator reads a URL one way, but the HTTP client/browser reads it another way (e.g., `https://trusted.com%40evil.com` or `http://127.1` which expands via `inet_aton` to `127.0.0.1`).
*   *Defense*: Validate destination URLs using native OS socket resolver libraries (`socket.inet_aton`), reject percent-encoded userinfo in authority blocks, and prohibit private/loopback CIDRs.

---

## 2. System Architecture Overview: LinkOps

LinkOps is an enterprise, high-throughput URL shortening and click analytics platform built with **FastAPI**, **PostgreSQL 16**, **Redis 7**, and a dedicated background **Python Worker**.

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
   Public, No Auth, <5ms                    Authenticated (API Key), Tenant-Bound
               │                                         │
               ├───────────────┐                         │
               ▼               ▼                         ▼
         ┌───────────┐   ┌───────────┐             ┌───────────┐
         │ Redis     │   │ Postgres  │             │ Postgres  │
         │ Cache     │   │ resolve() │             │ (RLS ON)  │
         └───────────┘   └───────────┘             └───────────┘
               │ (Cache Miss)
               ▼
         [ Async Event ]
               │ LPUSH queue:clicks
               ▼
         ┌───────────┐
         │   Redis   │
         │   Queue   │
         └─────┬─────┘
               │ BRPOPLPUSH
               ▼
         ┌───────────┐
         │ Analytics │ ─── Idempotent Rollup ───►  Postgres
         │  Worker   │                             (Partitioned click_events
         └───────────┘                              + analytics table)
```

### The Two-Plane Model
*   **Data Plane (`/r/{code}`)**: The consumer-facing redirect route. Must be blazingly fast (<5ms). Short codes are globally unique (`uq_links_code`). `tenant_id` is an **OUTPUT** of the resolution. Handled without tenant context via `SECURITY DEFINER resolve_link()`.
*   **Management Plane (`/links`, `/links/{id}/analytics`)**: The admin CRUD plane. `tenant_id` is an **INPUT** extracted from the API key. Enforces strict PostgreSQL Row-Level Security on all tables.

---

## 3. Skill 1: System Design Fundamentals (Modules 01–10)

This skill covered the end-to-end design and implementation of the LinkOps architecture.

### Module 01: Architecture & Clean Code
*   **Decision**: Single-App Repo (`API_ROOT = api/`) over Multi-App Microservices.
    *   *Rationale*: The roadmap produces one deployable service with two entry points (API and Worker). Sharing models via imports prevents schema drift: if the API and Purge Worker hold different definitions of tenant scoping, the worker can delete another tenant's rows. Reversibility asymmetry: single-app to multi-app is a simple refactor; multi-app to single-app is costly.
*   **Bug Encountered (BREAK)**: `APP_PORT` vs `PORT`. `.env` used `APP_PORT`, but Pydantic settings read `PORT`.
    *   *Fix*: Established strict **Configuration Key Parity**. Added startup validation comparing `.env` keys against Pydantic fields. Missing or misspelled keys fail immediately at boot.
*   **Probe Design**: Established that `/health` must be dependency-free, while `/ready` checks hard dependencies only.

### Module 02: Database Selection & Schema Design
*   **Decision**: PostgreSQL over MongoDB.
    *   *Rationale*: Constraints must belong to data, not code. `NOT NULL UNIQUE` guarantees no links exist without codes. PostgreSQL provides native **Row-Level Security (RLS)** with `FORCE ROW LEVEL SECURITY`, guaranteeing that even if application code omits a tenant filter, Postgres returns 0 rows.
*   **Primary Keys**: Selected **UUIDv7** over UUIDv4. Time-ordered prefix preserves sequential B-tree writes and eliminates random page splits.
*   **Bug Encountered (BREAK)**: Injected live RLS policy drift: `ALTER POLICY tenant_isolation ON links WITH CHECK (true)`.
    *   *Diagnosis*: `USING` was intact (reads filtered), but `WITH CHECK` was true (writes unfiltered). Tenant B could write into Tenant A's account. Standard `alembic upgrade head` reported clean because Alembic only tracks revision IDs, not live catalog definitions.
    *   *Fix*: Created migration `0003_restore_with_check.py` and built automated verification script `check_rls.py` to inspect `pg_policies` directly.

### Module 03: Core API & Routing
*   **Decision**: Prefix Route (`/r/{code}`) over Root Route (`/{code}`).
    *   *Rationale*: A root route merges the dynamic short-code namespace with the application route namespace. Future routes (`/health`, `/billing`, `/teams`) would become reserved words, risking collisions with existing user links. A prefix route separates data and management namespaces cleanly.
*   **HTTP Status Code Decisions**:
    *   Invalid destination URL: **400 Bad Request** (overriding FastAPI's default 422 to avoid leaking internal parser traces).
    *   Expired / Unknown / Disabled link: **404 Not Found** (byte-identical response). Avoided `410 Gone` because `410` confirms a code previously existed, enabling enumeration attacks.
*   **Security Fix (URL Policy)**: Found 3 SSRF/bypass vulnerabilities: `http://127.1` (expanded to loopback), `http://127.0.1`, and `https://good.com%40evil.com` (userinfo authority confusion). Fixed by delegating IP detection directly to `socket.inet_aton` and rejecting percent-encoding in authority blocks.

### Module 04: Authentication & Authorization
*   **Decision**: API Key authentication over stateless JWTs.
    *   *Rationale*: Instant revocation. Revoking an API key is a single database update effective on the very next request. Revoking a JWT requires a distributed denylist (Redis), which reintroduces state and defeats JWT's primary benefit.
*   **Security Implementation**:
    *   Format: Public `key_id` handle + SHA-256 secret hash.
    *   Constant-time comparison via `hmac.compare_digest` with dummy hash fallback on unknown keys to eliminate timing attack side-channels.
*   **The Identity Paradox & Fix**: `api_keys` table cannot be protected by RLS because reading the API key is what establishes identity!
    *   *Fix*: Revoked direct `SELECT` and `UPDATE` on `api_keys` from `upsk_app`. Encapsulated lookup inside `verify_api_key(text)` as a `SECURITY DEFINER` function with fixed search path.

### Module 05: Error Handling & Logging
*   **Injected Bug (PII in Logs)**: In an invitation service, failed token attempts logged:
    ```python
    log.warning("invitation rejected", extra={"invited_email": row.email, "caller_email": user_email})
    ```
    *Impact*: Reopened the enumeration oracle in log files, violated GDPR processing agreements, and inflated log levels to WARNING for normal 404 client events.
*   **Fix**: Replaced identifiers with opaque UUIDs (`invitation_id`, `user_id`).
*   **Database Error Masking**: Found that `psycopg` exception details logged the full failing row containing sensitive data. Fixed by extracting `exc.orig.diag` metadata (`constraint_name`, `table_name`) and redacting row values.
*   **Standing Control**: Created `check_log_pii.py` to assert that log outputs never leak email formats.

### Module 06: Caching with Redis
*   **Decision**: Cache-Aside with explicit post-commit invalidation.
*   **The Cache vs. RLS Hazard**: Postgres enforces RLS; Redis does not. A cache hit bypasses Postgres completely. If tenant scoping is missing from cache keys, cross-tenant data leaks occur silently.
    *   *Fix*: Created `app/cache.py` with mandatory namespace registration:
        *   `redirect` namespace: explicitly marked global.
        *   `links_page` and `click_totals`: require `tenant_id`. Omitting tenant throws a fatal `CacheKeyError`.
*   **Thundering Herd Defense**: Built `fill_once()` single-flight deduplication (wait up to 50ms for the in-flight database query to finish).
*   **Invalidation Timing**: Registered invalidations on SQLAlchemy `after_commit`. Invalidating during transaction execution creates a race window where concurrent requests read uncommitted stale data into Redis.

### Module 07: Background Jobs & Click Analytics
*   **Decision**: Asynchronous queueing via Redis. Redirect enqueues click event; worker aggregates asynchronously.
*   **The Idempotency Ordering Principle**:
    *   Moved from `BRPOP` (at-most-once) to `BRPOPLPUSH` (at-least-once).
    *   *Critical Design Rule*: Made the write operation **idempotent before enabling retries**. Retrying a non-idempotent operation (`count = count + 1`) causes double-counting.
*   **Idempotent Rollup SQL**:
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
    ON CONFLICT (link_id, timestamp_bucket) DO UPDATE SET count = analytics.count + 1;
    ```
*   **Partition Purging**: `click_events` is range-partitioned monthly by `clicked_at`. Retention purging drops expired partitions (`DROP TABLE click_events_2026_07`) in milliseconds with zero VACUUM overhead.

### Module 08: Search & Advanced Queries
*   **Decision**: Database-native search using `pg_trgm` GIN indexes over Elasticsearch.
    *   *Rationale*: Avoided introducing another stateful system outside the Postgres RLS boundary. URLs are not natural prose (Elasticsearch/tsvector tokenization breaks on substrings like searching `xampl` in `example.com`). Trigram indexes support arbitrary substring matching.
*   **Security & Query Discipline**:
    *   Strict sort allowlisting (`sort` parameter accepts only `'created'` or `'clicks'`; rejects arbitrary SQL identifiers).
    *   Wildcards escaped: user input `%` or `_` is escaped to prevent wildcard injection.

### Module 09: Testing Strategy
*   **Decision**: Integration-heavy testing against real PostgreSQL container over unit tests with mocks.
    *   *Rationale*: Core guarantees (RLS isolation, unique constraints, ON CONFLICT idempotency, trigram indexes) live in PostgreSQL. Mocking the database tests your mock assumptions, not system behavior.
*   **Safety Fixtures**:
    *   Transactional rollback: Each test wraps in a transaction that rolls back at teardown.
    *   Database Guard: Built `_guard_target_database` fixture to abort if `DATABASE_URL` points to production instead of `linkops_test`.

### Module 10: CI/CD & Deployment
*   **Decision**: Multi-process deployment (`web` and `worker`) from a single Docker image, connecting to managed Postgres and Redis.
*   **Failure Fast on Timeouts**: Configured `pool_timeout=5.0` and `connect_timeout=5.0` in `app/db.py`. Converted hangs into fast 503 errors.
*   **Readiness Isolation Bug (BREAK)**: Injected `redis.ping()` into `/ready`. When Redis stopped, `/ready` failed with 503, causing the load balancer to drop all pods even though redirects were functioning via the database.
    *   *Fix*: Removed Redis from `/ready`. Redis remains an internal soft dependency.

---

## 4. Skill 2: Debugging & Incident Response (Modules 01–07)

This skill focused on root-cause analysis, race conditions, silent data corruption, and production incident management.

### Key Bugs Diagnosed and Fixed

#### 1. Offset Pagination Drift Under Concurrent Inserts
*   *Symptom*: When a client paginates using `offset = page * limit` with `ORDER BY created_at DESC`, new inserts arriving at position 0 shift all items down. The client sees duplicate items on page 2.
*   *Fix*: Switched to keyset/cursor pagination using an `as_of` timestamp anchor and unique tie-breakers (`WHERE (created_at, id) < (:last_created_at, :last_id)`).

#### 2. Timezone Truncation Bug in Rollups
*   *Symptom*: Worker rolled up clicks using `DATE_TRUNC('hour', NOW())`. `NOW()` truncated in the local session timezone. Clicks occurring in the same hour split into different buckets depending on client session timezone.
*   *Fix*: Enforced `DATE_TRUNC('hour', clicked_at AT TIME ZONE 'UTC')` on explicit timestamps.

#### 3. Check-Then-Insert Race Condition
*   *Symptom*: Application checked if an analytics bucket existed with `SELECT`, then executed `INSERT`. Under concurrency, multiple threads saw no bucket and both inserted, causing `UniqueViolation` errors or lost updates.
*   *Fix*: Replaced application check-then-insert with atomic database upserts (`INSERT ... ON CONFLICT DO UPDATE`).

#### 4. Transaction Timestamp Freezing (`NOW()` vs `clock_timestamp()`)
*   *Symptom*: In PostgreSQL, `NOW()` returns the timestamp of when the *transaction began*, not when the statement executed. In long-running transactions or retries, `NOW()` was stale by hundreds of milliseconds.
*   *Fix*: Used `clock_timestamp()` for real-time wall-clock tracking combined with `GREATEST()` to ensure monotonic ordering.

### The SEV-1 Incident Postmortem (OrderProcessor Outage)
*   **Incident Summary**: For 94 minutes, an OrderProcessor service charged customers without creating orders (1,400 orders, ~$186,000 affected).
*   **Root Causes**:
    1.  *Broad Exception Handling*: A generic `try/except Exception` caught a missing config field error, logged it at `DEBUG`, and returned `200 OK`. The HTTP transport reported success for work it never performed.
    2.  *Monitoring System Health Instead of Business Metrics*: Dashboards tracked CPU, memory, latency, and HTTP status codes (all green). No alert existed for `orders_created_per_minute == 0`.
    3.  *Untested Rollback Scripts*: During the incident, automated rollback failed because script paths were stale, extending the outage by 26 minutes.
*   **Remediation**:
    *   Strict exception typing (fail-closed).
    *   Alerting on business transactions (SLO on order creation rate).
    *   Automated CI smoke tests verifying deployment and rollback scripts.

---

## 5. Skill 3: Technical Communication (Modules 01–06)

This skill developed high-impact engineering communication artifacts.

### 1. Lightweight RFC: Rate Limiting Public APIs ([design-doc.md](file:///Users/himanshusharma/caw/upsk-system-design-workspace/design-doc.md))
*   **Problem**: A single customer sending 50,000 req/min degraded the shared API.
*   **Architecture**: Fixed-window counter in Redis keyed by specific attack surfaces:
    *   Auth failures -> keyed by `key_id` (10/min) -> protects against credential stuffing.
    *   Writes -> keyed by `tenant_id` (60/min) -> protects against shared-domain blocklisting.
    *   Public reads -> keyed by client IP (600/min) -> protects against short-code enumeration.
*   **Key Trade-off**: Fail-open on Redis loss. A rate limiter must never take down the API when its own dependency fails.

### 2. Blameless Incident Postmortem ([postmortem.md](file:///Users/himanshusharma/caw/upsk-system-design-workspace/postmortem.md))
*   Structured timeline tracking: Time-to-Detect (38m), Time-to-Diagnose (17m), Time-to-Mitigate (32m).
*   Applied Five-Whys methodology to isolate structural flaws rather than blaming individual engineers.

### 3. Actionable On-Call Runbooks ([RUNBOOK-orderflow.md](file:///Users/himanshusharma/caw/upsk-system-design-workspace/RUNBOOK-orderflow.md))
*   **Rule**: Designed for a paged engineer at 3 AM.
*   Contains exact, copy-pasteable commands with 60-second triage checklists, rollback procedures, and clear escalation thresholds.

### 4. Technical Architecture Explanations ([technical-explanation.md](file:///Users/himanshusharma/caw/upsk-system-design-workspace/technical-explanation.md))
*   Explained a REST-to-GraphQL migration to onboarding engineers, articulating trade-offs: eliminating 15 ad-hoc aggregation endpoints vs. losing edge HTTP caching.

---

## 6. Skill 4: AI-Augmented Engineering (Modules 01–08)

This skill focused on using AI coding tools safely in mission-critical environments.

### Core Principles
1.  **AI Code Audits**: Ran a 17-finding structural review over generated code, discovering 2 critical runtime bugs:
    *   Missing RLS policies on newly generated auxiliary tables (`teams`, `team_memberships`, `invitations`).
    *   Unhandled database constraint exceptions leaking raw database error strings to users.
2.  **Verification Harnesses**: Never trust generated code without a failing test. Re-inject the defect, verify that the test exits non-zero (red), apply the fix, and verify it exits zero (green).
3.  **Prompt Constraints for Multi-Tenancy**: When prompting LLMs for multi-tenant systems, explicitly supply the isolation constraints: *"All database operations must run against a session with `app.tenant_id` bound. Handlers must not filter by tenant manually; RLS must enforce it."*

---

## 7. Skill 5: Decomposition & Execution Planning (Modules 01–08)

This skill applied software engineering management principles to a two-sided marketplace scenario (SkillSwap).

### Core Methodologies
1.  **Vertical Slicing over Horizontal Layering**:
    *   *Horizontal*: Database layer first, then API layer, then UI layer. Delivers zero usable value until the very end.
    *   *Vertical*: Build a thin, end-to-end slice (e.g., Post a Skill -> View a Skill) across all layers. Validates architecture early and allows continuous delivery.
2.  **Dependency DAGs & Critical Path Analysis**:
    *   Mapped task dependencies to identify blockers. Ensured data contracts and migration schemas were finalized before parallel frontend and backend development began.
3.  **Blast Radius & Plan Adaptation**:
    *   Evaluated mid-project requirement changes (e.g., introducing team invitations) by calculating blast radius across database schemas, API contracts, and background workers before updating project milestones.

---

## 8. Skill 6: Production Readiness (Modules 01–08)

This skill prepared LinkOps for zero-downtime, resilient production operation.

### Containerization Strategy (Dockerfile)
*   **Base Image**: Selected `python:3.14-slim` (Debian) over Alpine.
    *   *Why?* High-performance Python packages (`psycopg[binary]`, `pydantic-core`, `uvloop`) ship pre-compiled wheels for `glibc`. Alpine uses `musl`, forcing `pip` to compile C/Rust extensions from source, bloating image build times and requiring compilation toolchains in production images.
*   **Multi-Stage Build**:
    *   *Builder stage*: Installs build dependencies (`gcc`, `libpq-dev`) and installs Python wheels into a virtualenv.
    *   *Runner stage*: Copies only the clean virtualenv into a minimal runtime image. Contains zero compilers, test tools, or development libraries.
*   **Security**: Runs as non-root user `appuser` (UID 10001).

### Failure Mode Analysis ("Slow vs. Down")
*   **Down**: Fails fast (TCP RST / immediate 503). Survived easily.
*   **Slow**: The dangerous failure mode. When PostgreSQL or external systems slow down without timeouts, requests back up, thread pools saturate, memory spikes, and the entire API becomes unresponsive.
*   **Defensive Measures**:
    *   Postgres: `pool_timeout=5.0`, `connect_timeout=5.0`.
    *   Redis: non-blocking calls with explicit timeouts; fail-open on redirect cache misses.
    *   Graceful Shutdown: Traps `SIGTERM`. Flips `/ready` to 503 so the load balancer stops routing traffic to the instance, waits 10 seconds for active requests to finish, then terminates.

---

## 9. Complete Codebase File Index & Component Guide

### 1. Application Core (`api/app/`)
*   [config.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/config.py): Pydantic `Settings`. Enforces required environment variables at boot with key parity; redacts sensitive secrets in logs.
*   [db.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/db.py): Engine creation, connection pooling with 5s timeout, and request-scoped `SET LOCAL app.tenant_id` session generator (`get_tenant_session`).
*   [models.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/models.py): SQLAlchemy models using **UUIDv7** primary keys (`Link`, `ClickEvent`, `AnalyticsRollup`, `ApiKey`, `Team`).
*   [main.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/main.py): FastAPI application setup, structured logging middleware, exception handlers, and probes (`/health`, `/ready`).
*   [auth.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/auth.py): API key authentication (`require_principal`), constant-time verification, and credential validation.
*   [cache.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/cache.py): Redis cache-aside implementation, single-flight thundering herd mitigation (`fill_once`), TTL jitter, and strict namespace governance.
*   [ratelimit.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/ratelimit.py): Fixed-window counter rate limiting in Redis with fail-open semantics.
*   [logging_config.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/logging_config.py): JSON formatter redacting PII and preventing CRLF log injection.
*   [rls.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/rls.py): Table-level Row-Level Security declarations.

### 2. API Routers & Services (`api/app/routers/` & `api/app/services/`)
*   [routers/redirect.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/routers/redirect.py): Handles `GET /r/{code}`. Queries cache -> falls back to `resolve_link()` -> pushes event to Redis queue -> returns 302 Found.
*   [routers/links.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/routers/links.py): Management plane CRUD: `POST /links`, `GET /links`, `PATCH /links/{id}`, `GET /links/search`, `GET /links/{id}/analytics`.
*   [services/url_policy.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/services/url_policy.py): Destination URL safety validation (blocks SSRF, private CIDRs, loopbacks via `socket.inet_aton`).
*   [services/links_service.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/services/links_service.py): Link creation, updates, and trigram search execution.
*   [services/clicks.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/services/clicks.py): Constructs click event payloads with anonymized, salted IP hashes.

### 3. Worker (`worker/`)
*   [worker/main.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/worker/main.py): Long-running background worker draining `queue:clicks` via `BRPOPLPUSH`. Executes single-statement idempotent rollups into partitioned tables.

### 4. Database Migrations (`api/alembic/versions/`)
*   `0001_initial_schema.py`: Base tables with UUIDv7, `uq_links_code`, and range-partitioned `click_events`.
*   `0002_rls_empty_guc.py`: Initial RLS policies on `links` and `click_events`.
*   `0003_restore_with_check.py`: Restores both `USING` and `WITH CHECK` clauses on policies.
*   `0007_api_keys.py`: API keys table.
*   `0008_verify_api_key.py`: `SECURITY DEFINER` API key verification function.
*   `0010_analytics_tenant_scope.py`: Enforces tenant-scoping RLS on analytics rollups.
*   `0011_search_indexes.py`: Adds `pg_trgm` extension and GIN trigram indexes.
*   `0012_teams_rls.py`: Adds RLS to `teams`, `team_memberships`, and `invitations`.

### 5. Automated Standing Controls (`api/scripts/`)
*   [check_rls.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/scripts/check_rls.py): Verifies live database policies have RLS enabled, forced, and dual-scoped.
*   [check_url_policy.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/scripts/check_url_policy.py): Tests 43 adversarial URL payloads against SSRF validation.
*   [check_log_injection.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/scripts/check_log_injection.py): Asserts malicious newline payloads cannot split JSON log records.
*   [check_log_pii.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/scripts/check_log_pii.py): Scans log output for email patterns and sensitive details.
*   [check_cache_scoping.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/scripts/check_cache_scoping.py): Verifies tenant-scoped cache keys cannot be constructed without tenant IDs.
*   [check_analytics_pipeline.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/scripts/check_analytics_pipeline.py): Verifies queue agreement, at-least-once recovery, and idempotent rollup behavior.
*   [purge_click_events.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/scripts/purge_click_events.py): Drops expired `click_events` partitions.

---

## 10. End-to-End Runtime Execution & Data Flows

### Trace 1: The Fast Redirect Path (`GET /r/{code}`)
1.  **Request Arrival**: User clicks `https://linkops.internal/r/promo2026`.
2.  **Routing**: Traverses reverse proxy to FastAPI instance running [routers/redirect.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/routers/redirect.py).
3.  **Cache Lookup**: Calls `cache.get("redirect:promo2026")`.
    *   *If Cache Hit*: Returns `302 Found` with `Location: https://target.com/landing` immediately (<2ms).
    *   *If Cache Miss*: Calls `resolve_link('promo2026')` in PostgreSQL. This `SECURITY DEFINER` function checks link status without requiring a tenant context.
    *   Stores result in Redis with 60s + 10s jitter. Returns `302 Found`.
4.  **Asynchronous Click Logging**:
    *   In the background, `enqueue_click()` constructs an event payload (UUIDv7 `event_id`, salted IP hash, truncated user-agent, `clicked_at` timestamp).
    *   Executes `LPUSH queue:clicks` into Redis. Fails soft if Redis is down (redirect is never blocked).

### Trace 2: The Click Analytics Rollup Worker
1.  **Queue Dequeue**: [worker/main.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/worker/main.py) calls `BRPOPLPUSH queue:clicks queue:clicks:processing 5`.
2.  **Database Upsert**: Worker begins transaction and runs the idempotent CTE:
    *   Attempts insert into `click_events` partition. If `(id, clicked_at)` conflicts, does nothing.
    *   Pipes successfully inserted rows into `analytics` table, incrementing hourly bucket count.
3.  **Queue Acknowledgment**: Transaction commits. Worker calls `LREM queue:clicks:processing 1 <payload>`.
4.  **Error Recovery**: If worker crashes mid-transaction, Postgres rolls back. On reboot, an orphan recovery process scans `queue:clicks:processing` and re-queues unfinished jobs.

### Trace 3: Authenticated Link Creation (`POST /links`)
1.  **Authentication**: Caller sends `POST /links` with `X-API-Key: lk_live_abc123...`.
2.  **Auth Middleware**: [auth.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/auth.py) extracts `key_id`, queries `verify_api_key(key_id)`, verifies secret with constant-time compare, and returns `tenant_id`.
3.  **Session Binding**: [db.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/db.py) acquires a connection from the pool and sets `SET LOCAL app.tenant_id = '<tenant_id>'`.
4.  **Destination Validation**: [url_policy.py](file:///Users/himanshusharma/caw/upsk-system-design-workspace/api/app/services/url_policy.py) ensures destination is valid and non-internal.
5.  **Persistence**: Inserts link into `links` table. PostgreSQL RLS verifies `tenant_id` matches the session GUC.
6.  **Cache Invalidation**: Database transaction commits. SQLAlchemy `after_commit` hook fires, clearing any existing Redis keys for this code.
7.  **Response**: Returns `201 Created` with generated short URL.

---

## 11. Top 20 Interview Questions & Battle-Tested Answers

### Architecture & Multi-Tenancy
**Q1: Why did you choose Row-Level Security over multi-database or application-level tenant isolation?**  
> "Multi-database creates massive operational overhead when scaling to thousands of tenants. Application-level filtering (`WHERE tenant_id = ?`) relies entirely on developer discipline; one missed filter exposes all tenant records. By utilizing PostgreSQL RLS with `FORCE ROW LEVEL SECURITY`, isolation is enforced at the database engine level. A query omitting a tenant filter returns zero rows, preventing cross-tenant data leaks by default."

**Q2: What is the difference between `USING` and `WITH CHECK` in PostgreSQL RLS policies?**  
> "`USING` controls which existing rows can be read by `SELECT`, `UPDATE`, or `DELETE`. `WITH CHECK` validates newly created or modified rows during `INSERT` or `UPDATE`. If you only define `USING`, a malicious or misconfigured tenant can insert rows with another tenant's `tenant_id` and successfully commit them, even though they cannot read them back. Both clauses must be defined identically."

**Q3: What is the 'identity bootstrap paradox' in multi-tenant databases?**  
> "The `api_keys` table holds the credentials that establish caller identity. If you place an RLS policy on `api_keys` requiring `tenant_id = current_setting('app.tenant_id')`, you can never read the API key because the tenant is unknown until the key is read. We solved this by revoking direct table access from the application role and exposing a tightly scoped `SECURITY DEFINER` function `verify_api_key()` to authenticate credentials before binding the tenant GUC."

**Q4: Why use UUIDv7 instead of UUIDv4 for database primary keys?**  
> "UUIDv4 generates purely random values, which scatters inserts across random pages of a B-tree index, causing constant page splits and high I/O write amplification. UUIDv7 embeds a millisecond-precision Unix timestamp in the high-order bits. This makes primary keys time-ordered, ensuring sequential B-tree inserts while preserving distributed uniqueness."

### Caching & Redis
**Q5: How can caching in Redis break multi-tenant security?**  
> "PostgreSQL enforces RLS, but Redis has no native multi-tenancy. A cache hit bypasses PostgreSQL completely. If cache keys are not strictly scoped by tenant, a read from Tenant B can return cached data belonging to Tenant A. We mitigated this by building an enforced namespace registry where tenant-scoped caches throw a fatal `CacheKeyError` if constructed without an explicit `tenant_id`."

**Q6: Why did you choose Cache-Aside instead of Write-Through caching?**  
> "Write-through puts Redis directly in the critical write path. If Redis experiences downtime, database writes fail or become inconsistent. Cache-aside makes Redis an optional performance optimization: if Redis crashes, the application catches the failure and falls back to PostgreSQL. Cache failure degrades latency, but never availability."

**Q7: How did you handle the Thundering Herd problem on popular links?**  
> "We implemented single-flight request coalescing (`fill_once`). When a hot key expires in Redis, the first thread to miss acquires a lightweight lock and queries PostgreSQL, while concurrent requests wait up to 50ms for that single query to populate the cache. We also added ±10s random jitter to TTLs to prevent synchronized expiration storms."

**Q8: Why must cache invalidation occur *after* database commit rather than during the write?**  
> "If you invalidate a cache key *before* or *during* the database transaction, a concurrent request can miss in cache, query the database, read the old uncommitted data, and write that stale data back into Redis. Using SQLAlchemy's `after_commit` hook ensures the cache is cleared only after the database write is durable."

### Queues, Workers & Reliability
**Q9: Why is it dangerous to enable at-least-once queue delivery before write idempotency?**  
> "At-least-once delivery guarantees a job will not be lost, but introduces duplicate message deliveries during network retries or worker restarts. If your worker performs a non-idempotent operation like `count = count + 1`, every duplicate delivery corrupts the data by double-counting. Idempotency must always be established before at-least-once processing."

**Q10: How does your worker achieve atomic idempotency for click analytics?**  
> "We use a single Common Table Expression (CTE) in PostgreSQL. The CTE inserts into `click_events` using `ON CONFLICT (id, clicked_at) DO NOTHING RETURNING link_id`. The outer query inserts into the `analytics` rollup counter only for rows returned by the CTE. If an event is replayed, the CTE insert conflicts, returns zero rows, and the rollup counter does not increment."

**Q11: Why did you use PostgreSQL table partitioning for click events?**  
> "High-volume append-only event logs cause tables to grow into hundreds of millions of rows. Executing `DELETE FROM click_events WHERE clicked_at < cutoff` causes massive WAL write volume, table locks, and index fragmentation. By range-partitioning by month, our retention purge script drops an entire partition table via `DROP TABLE click_events_YYYY_MM`. This completes in milliseconds with zero VACUUM overhead."

### Networking, Security & APIs
**Q12: How did you defend against Server-Side Request Forgery (SSRF) in URL validation?**  
> "Attackers exploit parser mismatches between regex validators and operating system network resolvers (e.g., using octal notations like `0177.0.0.1`, decimal formats like `127.1`, or IPv6 mappings). We validate destination IPs using Python's native `socket.inet_aton` against private, loopback, and cloud metadata CIDR blocks. We also reject percent-encoded userinfo in authority blocks (e.g., `https://trusted.com%40evil.com`)."

**Q13: Why did you return 404 instead of 410 for expired or disabled links?**  
> "Returning `410 Gone` tells the caller that the short code definitely existed in the past, allowing attackers to systematically enumerate valid codes. Returning an identical `404 Not Found` for nonexistent, disabled, and expired links conceals code existence, closing the enumeration oracle."

**Q14: How do you prevent timing attacks on API key authentication?**  
> "If an unknown API key returns 401 in 2ms while an invalid secret on a known key returns 401 in 15ms (due to SHA-256 hashing), an attacker can enumerate valid key IDs by measuring response latency. We mitigate this by looking up keys by an unhashed `key_id`, and on miss, hashing a dummy string so that every 401 response executes in constant time."

### Operations & Production Engineering
**Q15: What is the operational difference between a `/health` and `/ready` endpoint?**  
> "`/health` is a liveness probe with zero dependencies. If it fails, the container orchestrator restarts the container. `/ready` is a readiness probe that checks whether the service can currently serve traffic (e.g. database pool connectivity). If `/ready` fails, the load balancer stops sending requests to the pod without restarting it."

**Q16: Why did you exclude Redis from the `/ready` check?**  
> "Redis is an optimization (cache) and an asynchronous queue. If Redis crashes, LinkOps can still serve redirects directly from PostgreSQL. If Redis were included in `/ready`, a temporary Redis outage would cause all API pods to report unreadiness simultaneously, converting a partial cache degradation into a total platform outage."

**Q17: Why is 'slow' more dangerous than 'down' in distributed systems?**  
> "When a dependency is down, it rejects connections immediately (TCP RST) and error handlers fire instantly. When a dependency is slow, incoming requests wait, holding open HTTP worker threads and database connections. This causes pool exhaustion to cascade upstream, crashing the entire application. We mitigate this by enforcing strict 5-second connection and pool timeouts."

**Q18: Why choose Debian-slim over Alpine Linux for Python container images?**  
> "Modern Python performance packages (`psycopg[binary]`, `pydantic-core`, `uvloop`) publish pre-compiled `glibc` binary wheels. Alpine uses `musl` libc, which forces `pip` to compile C and Rust extensions from source during build. This inflates build times and requires shipping heavy compilers (`gcc`, Rust) into build environments. Debian-slim provides pre-compiled wheel compatibility with minimal footprint."

**Q19: What were the key lessons from the SEV-1 OrderProcessor incident?**  
> "First, never catch generic exceptions to return an HTTP 200: transport status must reflect actual domain completion. Second, monitor business outcomes (orders created per minute) rather than purely system metrics (CPU, HTTP 200 rate). Third, rollback mechanisms must be continuously exercised in CI, or they will fail during an emergency."

**Q20: Why implement database-native trigram search instead of Elasticsearch?**  
> "Elasticsearch would introduce another stateful service outside our PostgreSQL Row-Level Security perimeter, creating data synchronization latency and risk of cross-tenant exposure. Furthermore, URLs are not natural language: standard full-text search tokenizers fail on arbitrary substrings (e.g., matching `xampl` inside `example.com`). PostgreSQL `pg_trgm` GIN indexes provide immediate consistency and native arbitrary substring search inside the RLS boundary."
