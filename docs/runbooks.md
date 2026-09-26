# LinkOps — Incident Runbooks

Decision-tree format (Module 07 DECIDE), branching on **observable** symptoms —
what you can read off a command, not what you think is wrong. Top 3 failure modes
by risk (probability × impact) from `../failure-mode-analysis.md`. Every command
is copy-pasteable. **Last verified: 2026-08-18.**

## Triage (start here)

```
curl -sS -o /dev/null -w "%{http_code}\n" https://<host>/ready
```
- **`503`** → readiness failing → dependency issue → **Runbook 1** (Postgres).
- **`200` but users report slow/failed redirects** → **Runbook 2** (Redis / hot path).
- **`200`, redirects fine, but analytics counts frozen** → **Runbook 3** (worker).

---

## Runbook 1 — Postgres unavailable or slow

### Detection
- Alert: `readiness_failing` (5xx on `/ready`) and/or `db_5xx_rate` climbing.
- Symptom: management endpoints (create/list/search) return 500/503; `/ready` 503;
  redirects may still work on warm cache.

### Diagnosis
**Step 1 — is the DB reachable from the app?**
```
kubectl exec -n production deploy/linkops-api -- \
  python -c "import os,psycopg; psycopg.connect(os.environ['DATABASE_URL']).close(); print('DB OK')"
```
- Prints `DB OK` → DB reachable; suspect **slow/read-only**, go to Step 3.
- Errors within ~5s (bounded by connect_timeout) → DB unreachable, Step 2.

**Step 2 — is Postgres up?**
```
kubectl get pods -n production -l app=linkops-postgres
```
- Not `Running` → **database incident**: page `#data-eng`. Do **not** restart the
  API — it cannot fix a down database.

**Step 3 — read-only / slow?**
```
kubectl exec -n production deploy/linkops-api -- \
  psql "$DATABASE_URL" -c "SHOW default_transaction_read_only;"
```
- `on` → primary is read-only (failover). Reads work, writes 500 — the monitoring
  blind spot. Route writes to the primary / end the failover; page `#data-eng`.

### Mitigation
- The service already fails fast (5s timeouts) rather than hanging, and `/ready`
  has pulled failing instances from rotation. Recovery is automatic once Postgres
  returns; no restart needed.

---

## Runbook 2 — Redis down (redirects OK, analytics dropping)

### Detection
- Alert: `redis_unreachable` / breaker `analytics circuit closed -> open` (ERROR).
- Symptom: redirects still 302 (this is expected!); click counts stop; logs show
  `cache read failed, serving from database` and `circuit open: click not counted`.

### Diagnosis
**Step 1 — confirm Redis, not the redirect path:**
```
curl -s -o /dev/null -w "%{http_code}\n" https://<host>/r/<known-code>    # expect 302
kubectl exec -n production deploy/linkops-api -- redis-cli -u "$REDIS_URL" ping
```
- Redirect `302` + `ping` fails → **this is Redis, and the service is degrading
  correctly.** The breaker is protecting the hot path.

### Mitigation
- **Redis being down is not a user-facing outage for LinkOps** — redirects serve
  from Postgres and the breaker keeps the hot path fast. Restore Redis:
  ```
  kubectl rollout restart -n production deploy/linkops-redis   # or page #data-eng
  ```
- Analytics resumes automatically within ~30s of recovery (breaker half-opens).
  Clicks during the outage are **lost, not delayed** — accepted by design.
- **Do not** add Redis to `/ready` to "fix" this — that would convert a
  survivable cache outage into a fleet-wide outage.

---

## Runbook 3 — Analytics worker stopped (counts frozen)

### Detection
- Alert: `analytics_queue_depth` climbing with no drain; `dead_letter_growth`.
- Symptom: redirects fine, `/ready` 200, but click counts do not advance.

### Diagnosis
**Step 1 — is the worker alive?**
```
kubectl get pods -n production -l app=linkops-worker
kubectl logs -n production deploy/linkops-worker --tail=50
```
- Not `Running` / no `worker: draining` line → worker down, Step 2.
- Running but silent, or repeated `dead-lettered` → a poison job or a stuck loop.

### Mitigation
```
kubectl rollout restart -n production deploy/linkops-worker
```
- On restart the worker **recovers orphaned jobs** from the processing list and
  drains the backlog — **no clicks are lost** while the worker was down, only
  delayed (BRPOPLPUSH parks jobs durably).
- If the dead-letter list is growing, a poison job is looping: inspect
  `analytics:dead` for the payload; it is a bug report with the data attached.

### Escalation (all runbooks)
No recovery in **15 minutes** or growing user impact → page `#linkops-oncall`,
then per layer: DB → `#data-eng`, ingress/registry → `#platform-infra`.

---

## Keeping this runbook honest (the anti-drift discipline)

A runbook fails the way GitLab's did in 2017: the system is renamed, the doc is
not, and a correct command runs cleanly against a target that no longer exists —
returning nothing, not an error. When a diagnosis command here returns **empty**
or **not found** (not a permission or timeout error), suspect the **document**
before the system: a name drifted.

Three defenses baked in:

1. **Last-verified date**, defined as *the day someone actually ran these
   commands*, not the day the words changed. Re-verify every 90 days; a runbook
   nobody re-runs is a claim, not a control.
2. **Names live once.** Env var names (`DATABASE_URL`, `REDIS_URL`) and resource
   labels (`app=linkops-postgres`, `deploy/linkops-worker`) trace back to
   `../api/.env.example` and the Helm values — this runbook references them, it
   does not redefine them. A rename is one place to update.
3. **Executable where possible.** The health/ready/breaker signals these runbooks
   branch on are exercised by `check_readiness_contract.py` and the test suite in
   CI, so a rename that breaks them fails the build the day it happens — not
   silently at 3 AM months later. Prose steps are the residue that can still
   drift; the last-verified date is their only guard.

**If a command here is stale:** read the live name off the running system
(`kubectl exec … printenv`, `kubectl get pods --show-labels`), fix the one line,
and bump the last-verified date.


---

## Runbook 4 — "Green dashboard, broken service" (Module 08 capstone)

Three failures that all pass `/ready` and show green. The theme: the signal is
shallower than the failure.

### 4a — /ready passes but real requests error
- **Signal:** `http_requests_total` 5xx rate climbing while `/ready` is 200.
- **Diagnosis:** `/ready` runs a bare `SELECT` — it proves *connectivity*, not
  *capability*. Compare it to what the failing request does. If reads pass and
  writes 500, the primary is **read-only** (Runbook 1, Step 3).
- **Fix:** keep `/ready` as the dependency gate; add a **post-deploy business
  smoke check** (create a link → resolve it → 302) run against the new version
  before it takes full traffic, and a **gated write-readiness probe** for
  write-serving instances. Readiness gates dependencies; the smoke check verifies
  behavior.

### 4b — Slow memory climb
- **Signal:** `process_resident_memory_bytes` trending up instead of plateauing
  (now exposed on `/metrics` — Linux/container only).
- **Diagnosis:** something accumulates per request. First suspect for a metrics
  service: **metric label cardinality** — hit `/metrics` and watch the series
  count; if it grows with unique traffic, a label has unbounded values. (LinkOps
  labels `path` by route template and keeps tenant out of labels precisely to
  avoid this.) Also rule out unclosed sessions and unbounded in-process caches.
- **Fix:** bound the offending label; move identifiers to logs. Alert on the
  memory metric so the next leak is a graph, not an OOM.

### 4c — Config changed outside the pipeline (drift)
- **Signal:** a running value differs from the declared contract.
- **Diagnosis:** compare running env to `../api/.env.example` and the pipeline
  env block; the key that differs was changed out-of-band (platform UI, not the
  pipeline). Dangerous because a redeploy silently reverts it.
- **Fix:** set the value **through the pipeline** (config as code), then redeploy
  so running == declared. `app/config.py`'s `check_key_parity` already fails the
  boot on a declared-vs-supplied mismatch; the prevention is that no config
  change is made anywhere but the pipeline.
