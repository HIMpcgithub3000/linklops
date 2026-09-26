# Module 04 — review pass over the Team Collaboration code

**Method:** `hybrid` — pattern pass over everything, line-by-line on the tests
first, then authorization, then the concurrency windows.

**Files reviewed:** `api/app/routers/teams.py`, `api/app/services/teams_service.py`,
`api/alembic/versions/0009_invitations.py`, `api/tests/test_teams_invitations.py`,
`api/alembic/versions/0006_teams_memberships.py` (T1, pre-existing).

**Everything below was executed, not read.** Two findings are runtime failures
with transcripts.

---

## File: `api/app/routers/teams.py`

| Category | Finding | Sev | Evidence |
|---|---|---|---|
| **AI red flag / Security** | **`Principal` does not exist. The module cannot be imported.** The code was generated against an identity model this codebase does not have. | **critical** | `ImportError: cannot import name 'Principal' from 'app.auth'` — `app/auth.py` exports `hash_secret`, `mint`, `require_principal`, `get_tenant_session` and no `Principal`. `require_principal` returns a **`str`** (the tenant id). |
| **Security** | Consequent, and worse than the import: `principal.user_id` and `principal.email` are used for every authorization decision and **neither exists anywhere in the system**. This service authenticates *tenants*, not *users* — there is no user identity to check a membership against. | **critical** | `require_role(db, team_id, principal.user_id, ...)`; `accept_invitation(..., principal.email)`. No `users` table, no user id in the API-key flow. |
| Error handling | `expires_at.isoformat().replace("+00:00","Z")` produces a **naive** timestamp with no suffix if psycopg returns a tz-naive value. Silent contract violation — the same `Z`-vs-`+00:00` class as Module 06. | high | `.replace()` on a string that may have no offset to replace. |
| Naming / API | `token: str \| None` is `None` on the re-invite path with no field saying why. A 201 arrives with no token and no explanation. | medium | `token=token or None` — an empty string overloaded as "already existed". |
| Edge cases | `ratelimit.check("create", ...)` reuses the **link-creation** limit (60/min, tenant-keyed) for invitations. Invitations trigger outbound email; the limit that protects link volume is not the limit that protects a mail reputation. | medium | `app/ratelimit.py` `LIMITS["create"] = (60, 60)`. |

## File: `api/app/services/teams_service.py`

| Category | Finding | Sev | Evidence |
|---|---|---|---|
| Edge cases | `require_role` does `ROLE_RANK[row.role]` — a **`KeyError` → 500** for any role in the CHECK constraint but not in the dict. Two places must be edited together and nothing enforces it. | high | Add a role to `ck_team_memberships_role` and every authorization check for that user 500s. `.get(row.role, 0)` fails closed instead. |
| Edge cases | Accepting an invitation for a role the user does not currently hold **silently does nothing** and returns 200 claiming the new role. `ON CONFLICT DO NOTHING` keeps the old row; the response says `role: admin` while the membership says `member`. **The API lies.** | high | A `member` accepts an `admin` invite → `AcceptResponse(role="admin")`, `team_memberships.role` still `member`. |
| Naming | `create_invitation` returns `(dict, "")` where the empty string means "this already existed". A sentinel value carrying control flow — the `result`-holds-three-things pattern. | medium | Callers must know `""` is meaningful. Should be an explicit `created: bool`. |
| Error handling | `revoke_invitation` calls `db.commit()` **before** checking `rowcount` and then raises 404. Harmless today (zero rows), but the commit is unconditional and the check is not. | low | Order is `execute → commit → if rowcount == 0: raise`. |

## File: `api/alembic/versions/0009_invitations.py` (and 0006)

| Category | Finding | Sev | Evidence |
|---|---|---|---|
| **Security** | **No row-level security on `teams`, `team_memberships` or `invitations`.** All three are tenant-scoped, all three grant `upsk_app` full DML, none has a policy or `FORCE ROW LEVEL SECURITY`. Any tenant's credentials read every tenant's teams and invitations. | **critical** | `app/rls.py`: `TABLES = ("links", "click_events")` — hardcoded. Three tenant-scoped tables joined the schema unexamined. |
| Security | `invitations.email` is stored at 320 chars with no format validation at the database boundary; `EmailStr` in the router is the only check, and the service is called directly by tests and could be called directly by future code. | medium | Validation lives at one of two entrances. |
| Edge cases | `expires_at` has no default and no check that it is in the future. An `INSERT` with a past `expires_at` is legal and creates a dead-on-arrival invitation. | low | `nullable=False`, no `CHECK (expires_at > created_at)`. |

## File: `api/tests/test_teams_invitations.py`

| Category | Finding | Sev | Evidence |
|---|---|---|---|
| **Tests** | **The tests cannot run.** They take `db` and `tenant` fixtures; there is no `conftest.py` anywhere in the repository, and `pytest` is not installed in the venv. Twelve tests that have never executed once. | **critical** | `find . -name conftest.py` → nothing. `python -m pytest` → `No module named pytest`. |
| Tests | The single-use guarantee is claimed by a **comment** — "two concurrent accepts both pass the checks, and exactly one updates a row" — and tested only sequentially. The concurrent case, which is the one the comment is about, has no test. | high | `test_invitation_is_single_use` calls accept twice in series. |
| Tests | `test_all_dead_token_paths_return_an_identical_body` covers 2 of the 5 paths it names in its own docstring (unknown, wrong recipient). Expired, revoked and already-accepted are absent. | medium | The docstring is a claim the test does not make good on. |
| Tests | No test asserts an **admin** can successfully invite — the whole file is negative. Correct emphasis, and it means a `require_role` that refused everyone would pass every test here. | medium | No positive authorization case. |

---

## What this pass says about the generator, and about me

Two of the four criticals are the same finding at different layers: **the code was
written against a system that does not exist.** `Principal` with a `user_id` is
what a team-collaboration feature *should* be built on, and this codebase
authenticates tenants by API key and has no concept of a user. The generator
produced fluent, well-structured, correctly-reasoned code for a plausible
adjacent system. Every comment is accurate about the code and wrong about where
it lives.

That is the failure mode the five categories do not name and that pattern-based
review misses completely: not a bug *in* a file, a mismatch between a file and
its surroundings. My own DECIDE answer predicted it — "all five categories are
properties of one function" — and I then generated exactly that class of defect.

**And the RLS finding is the fourth time.** `check_rls.py` inspects a hardcoded
list of two tables. I have proposed generalising it in four separate modules and
have not built it. Three tenant-scoped tables just entered the schema completely
unexamined, and nothing in the repository would ever have said so.
