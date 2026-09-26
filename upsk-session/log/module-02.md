# Module 02 — Database Design & Selection · in progress, step BREAK

## DECIDE: A, PostgreSQL

Query shapes are engine-neutral on retrieval — code lookup is a unique-index hit either
way, tenant-scoped list is a compound index scan either way. So the **constraints**
decide, not the queries:

- Uniqueness is a property of the data, not of the code that writes it. `NOT NULL UNIQUE`
  means a link with no code cannot exist. A document omitting the field inserts happily,
  and unique indexes treat missing as null — the second such write fails with a confusing
  duplicate-key-on-null.
- Decisive for `b2b_multi_tenant`: row-level security is the only mechanism in either
  engine that makes the Module 01 claim literally true — that isolation is structural
  rather than remembered. With `FORCE ROW LEVEL SECURITY` and a dedicated non-owning app
  role, a handler that forgets the tenant filter returns **zero rows** instead of
  **everyone's rows**. Mongo has no per-row equivalent; views are read-only, so writes
  still depend on discipline.

## BUILD / VERIFY — done

`progress/evidence/module-02/query-by-code.txt`: insert + select round-trip under a
tenant session, four negative controls, the public redirect path resolving without a
tenant bound, a click event landing in `click_events_2026_08`.

## BREAK — diagnosed and fixed; `upsk next` never ran

**Root cause:** `ALTER POLICY tenant_isolation ON links WITH CHECK (true)`, run by hand
directly against the database. `qual` untouched, so reads stayed sealed — which is why
three of four negative controls passed and only the write-side control caught it.

**Why that shape is the dangerous one:** keeping `USING` while losing `WITH CHECK` means
tenant B writes rows into tenant A's account and then cannot see them. Invisible to B,
invisible to every read-side test, surfacing only in A's dashboard as records nobody
there created. A read leak gets noticed; this doesn't.

**Why the repo looked clean:** it *was* clean. Migration file unmodified,
`alembic_version` read `0002` and was accurate. Alembic records which revisions ran, never
whether the schema still matches them — hand-run DDL is invisible to it permanently.

**Fix:** `0003_restore_with_check.py`, both tables, `USING` and `WITH CHECK` re-asserted.
Plus `api/scripts/check_rls.py` — asserts RLS enabled, FORCED, both clauses scoped. Verified
it fires by re-injecting identical drift:

```
healthy   -> RLS ok: links, click_events                                        exit=0
drifted   -> RLS DRIFT: links: WITH CHECK is not the tenant predicate -> 'true' exit=1
repaired  -> RLS ok                                                             exit=0
```

**Generalisation:** a migration proves the schema was *once* correct. For schema that is a
security control, something has to assert it's *still* correct. `alembic upgrade head`
returning clean is not that assertion.

## The injected bug was off-menu — reconciled 2026-08-13

The BREAK step text lists easy = wrong column type / missing env, medium = missing index,
hard = N+1 in a seed/list path. RLS drift is on none of them. Checked the live schema
against `models.py` rather than assuming: column types match (`code varchar(32)`,
`long_url text`, `clicked_at timestamptz`); all four declared indexes exist with
`indisvalid`/`indisready` true; all three `click_events` partitions carry all three
indexes attached, which is where a partition-level gap would hide while the parent still
looks correct; no query inside a loop in the seed path. Nothing on the menu is
outstanding.

The menu is adaptive-difficulty guidance and the instructor went past it — consistent
with Module 01, whose bug (`APP_PORT` vs `PORT`) was also off-menu, and with the pretest
calibration that set "select harder BREAK bugs earlier." Naming gap in these notes, not
an un-found bug.

## Two threads open — expect these to be asked

**Onset (the time-axis gap).** `SHOW log_statement` → `none`,
`log_min_duration_statement` → `-1`, `docker logs | grep -i "alter policy"` → 0 hits. The
grep leg is corroboration, not proof: the container started 2026-08-09 17:39 UTC and the
fix landed 2h46m later, so the injection could predate the log entirely. Honest line —
onset is unrecoverable, the reason is that DDL logging was off, and `log_statement = 'ddl'`
is the one-line change that makes the next one answerable. State the window, state the
fix, concede neither.

**Where `check_rls.py` runs.** Currently nowhere — it's manual. If asked, say so.

- **Not `/ready`.** It reads `pg_class` + `pg_policies`, so that's a catalog round trip
  per probe tick for a condition that changes never. Worse: drift isn't transient.
  Readiness means "temporarily can't serve, come back"; a drifted policy is permanent
  until a human fixes it, so failing `/ready` silently pulls the instance from the load
  balancer and on-call sees unexplained capacity loss rather than "tenant isolation is
  off." That is carry-forward #3 from Module 01 pointed inward — cite it.
- **Boot** — fail-fast, refuse to start, same pattern as the `AUTH_BACKEND` guard already
  in `app/db.py`. Citing own code beats proposing a new mechanism.
- **CI** — post-`alembic upgrade` step, so a migration dropping a policy never merges.

Neither catches a 3am hand-run `ALTER`. The tier above is a Postgres **event trigger** on
`ddl_command_end`, priced honestly: needs superuser and cannot run inside a transaction
block, so it can't ride an alembic migration — separate autocommit bootstrap. Works
locally (`SHOW is_superuser` → on for `upsk_owner`), but Railway (Module 10's target)
doesn't grant superuser, so it's a platform bootstrap, not a migration. Scope by command
tag (`ALTER POLICY` / `CREATE POLICY` / `DROP POLICY`) or it fires on every DDL. Log to an
audit table first (makes the 3am hand-run recorded); `RAISE` blocks it outright but has a
superuser-DDL false-positive blast radius. Offer it, don't build it.

## Loose end

`git status` in the workspace: `api/` and `progress/` untracked, **zero commits on
`main`**. Module 09 is Testing and Module 10 is CI/CD; both assume history exists.
