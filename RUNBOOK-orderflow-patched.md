# OrderFlow Service — Incident Runbook (patched)

**Last verified:** 2026-08-18 — health check, DB check, and rollback command run
against staging and confirmed working. *(A "last verified" date, not "last
updated": updated says someone touched the words; verified says someone ran the
commands. Re-verify every 90 days — a runbook nobody re-runs is the GitLab 2017
failure waiting to happen.)*

**Owner:** Platform Payments Team

## Prerequisites

- Access to the production Kubernetes cluster (`kubectl` configured — confirm
  with `kubectl get ns production`).
- PagerDuty access for the `pd trigger` commands below (confirm you can log in
  before you need it).
- Slack access to `#orderflow-oncall`.

---

## Step 1: Verify service health

```
curl -s https://orderflow.prod.company.com/health | jq .
```

Healthy response (note: `cache` may read `disabled` — OrderFlow runs fine without
Redis, just slower and without rate limiting, so `cache` not being `connected` is
**not** an outage):

```
{ "status": "healthy", "database": "connected", "worker": "running" }
```

- Anything other than `status: healthy` → **Step 2**.
- No response at all (timeout / connection refused) → **Step 6 (complete
  outage)**.

## Step 2: Check application logs

```
kubectl logs -n production deployment/orderflow-api --tail=100
```

- `ConnectionRefusedError` naming the **database** → **Step 3**.
- `Connection refused` naming **Redis**, or refunds not processing → **Step 4**.
- `TimeoutError` to a downstream service → **Step 5**.
- `500 Internal Server Error`, especially right after a deploy → **Step 7**.

## Step 3: Database connection failure

```
kubectl exec -n production deployment/orderflow-api -- \
  python -c "import psycopg2; psycopg2.connect('$DATABASE_URL')"
```
*(Fixed: the variable is `DATABASE_URL`, not `DB_CONNECTION_STRING`. The old name
would print an empty string and send you debugging a "missing" variable that is
simply named differently now.)*

1. Check the database pod:
   ```
   kubectl get pods -n production -l app=orderflow-db
   ```
2. If `CrashLoopBackOff`, read its logs and hand off to Data Eng:
   ```
   kubectl logs -n production -l app=orderflow-db --tail=50
   ```
   → page **`#data-eng`**. Do **not** restart the API; it cannot fix a down
   database.
3. If the DB pod is healthy but the API cannot connect, confirm the variable is
   present:
   ```
   kubectl exec -n production deployment/orderflow-api -- printenv DATABASE_URL
   ```
   Empty or wrong → the deployment config needs updating (page
   **`#platform-infra`**).

## Step 4: Redis / worker failure  *(new — was missing entirely)*

Redis powers rate limiting and is the queue the refund worker reads. If it is
down, rate limiting stops and refunds stop processing.

```
kubectl exec -n production deployment/orderflow-api -- redis-cli -u "$REDIS_URL" ping
```
Expected: `PONG`.

If Redis is **not** responding:
1. Check the Redis pod:
   ```
   kubectl get pods -n production -l app=orderflow-redis
   ```
2. If down, read events and escalate:
   ```
   kubectl describe pod -n production -l app=orderflow-redis
   ```
   → **`#data-eng`** if Redis cannot be recovered.

Check the worker is processing refunds:
```
kubectl logs -n production deployment/orderflow-worker --tail=50
```
- `Task received` → healthy.
- `Connection refused` to Redis → worker cannot reach the broker (fix Redis
  first).
- No recent output → worker may have crashed. Restart it:
  ```
  kubectl rollout restart deployment/orderflow-worker -n production
  ```

**Impact while Redis / worker is down:** rate limiting is disabled (the API
accepts all requests — consider maintenance mode if traffic is high), and refund
processing halts. Refunds **queue and drain automatically** when Redis recovers —
**no data is lost**, customers see delays. Order-taking and payments are
unaffected.

## Step 5: Downstream service timeout (auth)

```
curl -s -o /dev/null -w "%{http_code}\n" https://auth.internal.company.com/health
```
If auth is down, OrderFlow cannot validate tokens and returns 503 to
authenticated requests.
1. Check `#auth-team` for known issues.
2. If none, page the Auth team: `pd trigger --service auth-primary`.
3. **This is a customer-facing outage** — open an incident and post impact in
   `#orderflow-oncall`. *(Corrected: the old "no action needed" was wrong; a
   total auth failure is exactly when you communicate and escalate.)*

## Step 6: Complete service outage (no response)

1. Pod status:
   ```
   kubectl get pods -n production -l app=orderflow-api
   ```
2. Zero pods running → inspect the deployment:
   ```
   kubectl describe deployment -n production orderflow-api
   ```
3. `ImagePullBackOff` → the image is missing or the registry is down → escalate
   **`#platform-infra`**. `CrashLoopBackOff` right after a deploy → **Step 7**.

## Step 7: Bad deployment — roll back

```
# CORRECT: reverts to the previous working Helm release. Does NOT deploy new
# code — it restores the exact image and config from the last good deploy.
helm rollback orderflow --namespace production
```
*(Fixed: the old step ran `helm upgrade --set image.tag=latest`, which redeploys
the just-broken image — rolling forward, not back.)*

Verify:
```
helm history orderflow --namespace production
curl -s https://orderflow.prod.company.com/health | jq .
```
Recovered → post in `#orderflow-oncall` which revision was bad. Still failing →
**Step 8**.

## Step 8: Escalation

Escalate when a step told you to, after **15 minutes** without recovery, or when
customer impact is growing.
1. Page OrderFlow on-call: `pd trigger --service orderflow-primary`.
2. Post in `#orderflow-oncall`: what you tried, current state, links to
   logs/dashboards.
3. Route by layer: database → `#data-eng`; ingress/registry → `#platform-infra`;
   else the service owner, **Platform Payments**.
