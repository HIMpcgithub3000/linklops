# Module 09 — Testing

System Design Fundamentals, Operator pack.

## Decision

`decisions.module_09.test_strategy = integration_heavy` — *selected by the
instructor under Himanshu's standing direction.*

Not a coverage philosophy, a consequence of where the logic lives. Nearly every
property that matters here is enforced by PostgreSQL: tenant isolation is an RLS
policy, idempotency is `ON CONFLICT`, code entropy is a CHECK, single-use
invitations are an `UPDATE ... WHERE` guard, ordering is an index. A unit test
with a mocked database asserts the mock. Pure functions with no database
dependency (URL policy, mention parser, redirect-code validator) stay unit
tested, because there a real database adds latency and proves nothing.

## The interlude, made into a fixture

The module's interlude: a suite pointed at the wrong database does not fail, it
mutates. Two protections, both named because they are different:

- **Rollback bounds the blast radius.** The `db` fixture wraps each test in a
  transaction that is always rolled back, so a run leaves no trace even against
  a populated database.
- **`_guard_target_database` bounds the target.** Session-scoped, autouse, it
  refuses to run against any database whose name does not look like a test
  database (`test` / `_ci`) unless `UPSK_ALLOW_NONTEST_DB=1` is set explicitly.
  The escape hatch is loud on purpose — a decision, not a default that drifts.

Proven both ways:

```
DATABASE_URL -> linkops        Exit: refusing to run ... 'linkops' is not a test database
DATABASE_URL -> linkops_test   32 passed
```

## Test database

`linkops_test` created and migrated to `0011 (head)`. The suite now runs against
it; the two URLs are the real ones with the database name swapped from `linkops`
to `linkops_test` (roles are cluster-wide, so the app-role password is shared).

## The six required behaviours (`tests/test_integration.py` + `test_retention.py`)

| required | test | enforced by |
|---|---|---|
| create a link | `test_create_link_persists_with_entropy` | CHECK `ck_links_code_entropy` |
| redirect correctness | `test_redirect_resolves_only_live_links` | `resolve_link` SECURITY DEFINER |
| auth 401 | `test_missing_or_bad_key_is_401` | `require_principal`, one 401 for all causes |
| **owner scoping / IDOR** | `test_tenant_cannot_touch_another_tenants_link` | RLS on `links` |
| retention | `test_purge_drops_expired_clicks_and_keeps_the_rollup` | partition drop |
| URL validation bypass | `test_url_policy_rejects_a_known_bypass` | `validate_destination` |

Two of these needed care to be true rather than vacuous:

- **The IDOR test caught a testing trap, not an app bug.** First run, B *did*
  read A's link — because `get_link` is `session.get`, which returns the
  identity-map–cached object without a query, so RLS was never consulted.
  Production has two tenants on two sessions with empty caches; the fix is
  `db.expire_all()` after rebinding, which models a fresh request inside one
  transaction. Without that the test would have passed for the wrong reason.
  The search half (`search_links` runs raw SQL, no cache) was correctly 0 from
  the start.
- **Retention can't use the rollback fixture.** The purge drops partitions with
  DDL, which inside the fixture's held transaction would deadlock or escape the
  rollback. That test owns its own connection on the migration role and cleans
  up in a `finally`, and still asserts on one link alone.

## Evidence

```
tests/  32 passed  (was 26: +6 this module)
guard fires on linkops, passes on linkops_test
7 controls exit 0 (unchanged; they run against live data, not the pytest DB)
ruff clean
```

## Carry-forward

- **CI does not run pytest yet** — it runs the four controls against a DB named
  `linkops`. Wiring the pytest suite in (which needs the DB named to satisfy the
  guard, plus a Redis service for `check_cache_*` and `check_analytics_pipeline`)
  is Module 10's job. The guard does not affect current CI, because CI runs no
  pytest.
- The `run_tests.sh` helper that swaps the DB name lives in the session
  scratchpad, not the repo. If the test workflow is kept, it belongs in `api/`
  or a `Makefile` target so it is not reinvented.
- IDOR is covered for `links`. `teams`/`team_memberships`/`invitations` still
  have no RLS (the `check_tenant_coverage` exit 1), so an equivalent IDOR test
  there would fail — which is the honest state, not a gap in this suite.

## BREAK / FIX — flaky test from shared state

**Injected.** Two tests appending to a module-level `_SEEN` list and asserting
its length. Root cause: state that outlives a single test.

**Symptom, shown not described.** Identical code:

```
definition order   2 passed
reversed order     2 failed   (AssertionError at len(_SEEN) == 2)
```

Invisible locally in the order the author wrote them; surfaces the first time CI
shards or a plugin randomises order — a test that passes and fails on the same
commit.

**Fixed** by removing it: every real test already isolates state in fixtures (a
fresh `db`, a per-test `run_tag`), which is the structural answer — no module
globals, so nothing to leak.

**Proved determinism**, and found a quieter instance of the same bug doing it.
Running the suite in reversed file order showed `26 passed, 6 skipped`: the
search tests' `tenant` fixture borrowed *any existing tenant*, so on a fresh
test database they skipped unless another test had left one around —
order-dependent *skipping*, the same flake one notch quieter. Fixed by having
that fixture own its tenant through `two_tenants`.

```
after the fix:
  search tests alone     6 passed   (previously skipped)
  reversed file order    32 passed  (previously 26 passed, 6 skipped)
  full suite 3x          32 passed each
```

## Final state

`tests/` **32 passed**, deterministic across order and repetition · 7 controls
exit 0 · ruff clean.
