# Iteration log — real-time team activity feed

Strategy: `restart_early`, classifier = **does the fix prompt require deletion?**

---

## Round 1 — the initial prompt

> Implement a real-time activity feed for the Team Collaboration feature in
> `api/app/routers/activity.py`. FastAPI, Python 3.12, SQLAlchemy 2.0, Postgres.
> Connected clients receive team events (invitation sent, member joined,
> invitation revoked) as they happen. Existing schema: `teams(id, tenant_id,
> name)`, `team_memberships(team_id, user_id, role)` PK `(team_id, user_id)`,
> `invitations(id, team_id, email, role, ...)`. Use the project's existing
> logging (`app/logging_config.py`) and error envelope.

**Iteration: 1 · Type: initial · Structural 2 · Surface 4**

What came back: a working WebSocket server. A Postgres `LISTEN`/`NOTIFY` trigger
on the events table; the server listens and broadcasts. Clean lifecycle
handling, good naming, real error handling.

Key issues found:
1. **Every connected client receives every team's events.** Not a scaling
   concern — a cross-tenant data leak.
2. Delivery is coupled to the table. Renaming a column breaks the wire contract.
3. Raw database rows as payloads — the schema *is* the API.
4. No back-pressure: an unbounded buffer per client.

**Decision: RESTART.**

**Reasoning.** Applying the classifier before writing anything: the fix prompt
would have to say *"stop using NOTIFY; publish from the service layer instead."*
That is a **deletion plus a replacement**, so it is a restart in disguise — and
doing it as a follow-up leaves the trigger approach in context while the agent
builds its replacement.

Issue 1 is the one that settles it, and it is not incidental. **A trigger fires
when the row is written, which is before any notion of who may see it exists.**
The layer producing the event structurally cannot filter it. That is the same
shape as `api_keys`: the layer that establishes a fact is the one layer that
cannot apply the protection that depends on it. No follow-up prompt reaches it.

Surface 4 is what makes this dangerous rather than valuable. Clean code and good
naming are the signals a reviewer uses as a proxy for quality, and here all of
them are present and none is informative. **Restarting means discarding the only
part I can see.** That is the sunk-cost feeling, precisely.

---

## Round 2 — the restart prompt

Not the same prompt. It carries what round 1 taught: name the boundary, forbid
the mechanism by name, and specify the authorisation model rather than assuming
one.

> Implement a real-time team activity feed in `api/app/routers/activity.py`.
>
> **Events are published from the SERVICE LAYER**, by an explicit
> `publish_event(team_id, kind, actor_id, **payload)` function called from
> `teams_service`. **Do not use PostgreSQL LISTEN/NOTIFY, database triggers, or
> any mechanism that reads the table directly** — the database schema must not
> be the wire contract, and a trigger fires before the identity that would
> filter the event exists.
>
> **Authorisation:** resolve the caller's team memberships once at connect, from
> `team_memberships`. A connection joins only those teams' channels, so
> cross-team delivery is impossible by construction rather than by a filter that
> could be forgotten. The socket accepts **no client commands** — a client that
> can send "subscribe to team X" is a client that can subscribe to a team it does
> not belong to. Provide `hub.revoke(team_id, user_id)` so an ended membership
> drops the team from every live socket for that user: a socket held open is a
> standing grant unless something ends it.
>
> **Back-pressure:** one bounded `asyncio.Queue` per connection (max 100) and one
> writer task per connection. A full queue drops that subscriber's event and logs
> it — never blocks the publisher, never buffers without limit. Reading and
> writing the same socket from two places corrupts frames.
>
> **Auth failures:** close with `1008` and one identical code for every cause.
>
> Do not implement persistence, replay, or a message backplane.

**Iteration: 2 · Type: restart · Structural 4 · Surface 3**

Key issues found:
1. `_authenticate` and `_teams_for` are `NotImplementedError` — **blocked, not
   incomplete.** This codebase authenticates *tenants* by API key and has no user
   identity; the Module 04 review found the same wall. Honest stubs beat a
   plausible fake.
2. **Single-process only.** Two uvicorn workers and a client on worker A never
   sees an event published on worker B — *silently*. Stated in the `Hub`
   docstring rather than discovered in staging.
3. No test exercises two concurrent subscribers, or `revoke` mid-stream.

**Decision: SHIP (with 1–3 recorded).**

**Reasoning.** Every issue is **additive** — implement the two resolvers, add a
Redis backplane behind the unchanged `Hub` interface, add tests. None requires
deleting anything, which is the classifier saying "refine" rather than "restart".
Structural 4 not 5 because the single-process limit is a real constraint on the
scaling story; it is a 4 rather than a 2 because the `Hub` is a seam, so the
backplane replaces an implementation rather than an architecture.

---

## What the log says

Two rounds, one restart, no spiral — and the restart was decided **before**
writing a fix prompt, by asking whether the prompt would contain a deletion.
That question is answerable at first read, which is the whole argument for
`restart_early` over the two-iteration rule: the iteration budget would have been
spent rediscovering what the classifier said in round 1.

The cost was real: round 1's surface quality was **4**, round 2's is **3**. I
threw away better-looking code for better-structured code, and the log is the
only place that difference is visible.
