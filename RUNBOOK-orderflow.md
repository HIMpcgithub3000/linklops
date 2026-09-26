# OrderFlow — On-Call Runbook

You were paged. You may have never touched this service. Follow the numbered
steps; do not read ahead. Every command is copy-pasteable. Replace nothing except
values in `<angle brackets>`.

**What OrderFlow is:** the service that turns "Place Order" into a shipped order —
it creates orders, takes payments, and processes refunds. If it is down,
customers cannot check out.

**Last verified:** 2026-08-18. If today is more than 90 days later, treat every
command as suspect and confirm against the deploy config before trusting it.
*(This line exists because a stale runbook is worse than none — GitLab, 2017.)*

---

## 0. First 60 seconds — is it actually down?

1. Check the health endpoint:
   ```
   curl -sS -o /dev/null -w "%{http_code}\n" https://orderflow.internal.company.com/health
   ```
2. Read the result:
   - **`200`** → the API process is alive. The problem is a dependency or a
     partial failure → go to **§1**.
   - **`503`** → the API is up but reports itself not ready → go to **§3**.
   - **timeout / connection refused / `000`** → the API is not answering at all
     → go to **§4**.

**What "healthy" looks like:** `/health` returns `200`, order-create p95 under a
second, the refund queue near empty. **Unhealthy symptoms:** checkout errors,
refunds stuck "pending" for more than a few minutes, or 5xx errors in the
dashboard.

---

## 1. API returns 200 but something is wrong (5xx errors, slow)

1. Look at the last 100 error lines:
   ```
   kubectl logs -n production deploy/orderflow --tail=100 | grep -Ei '"level":"error"|5[0-9][0-9]'
   ```
2. Check the recent error rate in Sentry (link in `#orderflow-oncall` topic).
3. Decide from the logs:
   - Errors mention **database / connection / pool / timeout** → go to **§2**.
   - Refunds are **queuing but not completing** → go to **§5**.
   - A spike started **right after a deploy** (check step 4 below) → go to **§6
     (roll back)**.
   - None of the above → escalate (**§7**).
4. When did the last deploy happen?
   ```
   helm history orderflow -n production | tail -5
   ```
   A 5xx spike within minutes of the newest revision means the deploy is the
   prime suspect → **§6**.

---

## 2. Database connection lost

Symptom: logs show connection refused, pool timeout, or `could not connect`.

1. Confirm the API cannot reach Postgres:
   ```
   kubectl exec -n production deploy/orderflow -- \
     python -c "import os,psycopg; psycopg.connect(os.environ['DATABASE_URL']).close(); print('DB OK')"
   ```
2. Decide:
   - Prints **`DB OK`** → the database is reachable; this is not your problem
     layer → go back to **§1** step 3.
   - Errors → the database is unreachable → continue.
3. Check whether Postgres itself is up (ask `#data-eng` if you lack access):
   ```
   kubectl get pods -n production -l app=postgres
   ```
4. If the DB pod is **not Running/Ready**, this is a database incident, not an
   app incident → page **`#data-eng`** and set the incident owner to Data Eng.
   Do **not** restart the API — it cannot fix a down database.
5. If the DB pod **is** Running but the API still cannot connect, the connection
   string or a network policy changed. Restart the API pods once to pick up
   current config:
   ```
   kubectl rollout restart -n production deploy/orderflow
   ```
6. Re-run **§0 step 1**. Recovered → done. Still failing → escalate (**§7**).

---

## 3. /health returns 503 (not ready)

1. 503 means the API is running but a dependency it needs is not ready. Check
   which:
   ```
   kubectl logs -n production deploy/orderflow --tail=50 | grep -i ready
   ```
2. If it names the **database**, go to **§2**. Redis does **not** cause 503 —
   OrderFlow runs without Redis (slower, no rate limiting), so a 503 is never a
   Redis problem.
3. If it clears on its own within 2 minutes (a rolling deploy settling), watch
   and confirm with **§0 step 1**. If it persists past 2 minutes, escalate
   (**§7**).

---

## 4. API not answering at all (timeout / refused)

1. Are the pods running?
   ```
   kubectl get pods -n production -l app=orderflow
   ```
2. Decide:
   - **0 pods Running** or all `CrashLoopBackOff` → the app cannot start. Look at
     why:
     ```
     kubectl logs -n production deploy/orderflow --tail=50 --previous
     ```
     A config/env error right after a deploy → **§6 (roll back)**. Otherwise
     escalate (**§7**).
   - **Pods Running but not answering** → likely networking/ingress. Escalate to
     **`#platform-infra`** (**§7**) — do not spend more than 5 minutes here alone.

---

## 5. Refunds queued but not processing

Symptom: refunds stuck "pending"; orders and payments are fine.

1. Is the worker alive?
   ```
   kubectl get pods -n production -l app=orderflow-worker
   ```
2. Decide:
   - **Not Running / CrashLoop** → restart it:
     ```
     kubectl rollout restart -n production deploy/orderflow-worker
     ```
   - **Running** → check it is consuming, not wedged:
     ```
     kubectl logs -n production deploy/orderflow-worker --tail=50
     ```
     No activity for minutes → restart it (command above).
3. After restart, queued refunds drain automatically — the queue is durable, so
   nothing is lost, it was only delayed. Confirm the pending count is falling. If
   it is not within 5 minutes, escalate (**§7**).

> Customer impact note for the incident channel: while the worker is down,
> refunds are **delayed, not lost**. Payments and order-taking are unaffected.

---

## 6. Roll back a bad deployment

Use this when a failure started right after a deploy.

1. Roll back to the previous release:
   ```
   helm rollback orderflow --namespace production
   ```
2. Wait for it to settle, then re-check health:
   ```
   kubectl rollout status -n production deploy/orderflow --timeout=120s
   curl -sS -o /dev/null -w "%{http_code}\n" https://orderflow.internal.company.com/health
   ```
3. `200` → recovered. Post in `#orderflow-oncall` that you rolled back and which
   revision was bad (from `helm history`). Still failing → escalate (**§7**); the
   deploy may not have been the cause.

---

## 7. Escalate

Escalate when: a step told you to, you have spent **15 minutes** without
recovery, or customer impact is growing.

1. Page the next responder: PagerDuty escalation policy **"OrderFlow Primary."**
2. Post in **`#orderflow-oncall`**: what you saw, which section you reached, what
   you have already tried.
3. Route by layer if known: database → **`#data-eng`**; ingress/networking →
   **`#platform-infra`**; anything else → the service owner, **Platform Payments**.

You are not expected to fix everything alone at 3 AM. Escalating with a clear
summary is a good outcome, not a failure.
