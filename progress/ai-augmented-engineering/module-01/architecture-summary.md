# Module 01 BUILD — architecture summary via agent, and whether to trust it

## The prompt I wrote

> Read this workspace and answer these five questions separately. Do not summarise
> generally. For each answer, name the specific files you read.
>
> 1. Top-level directory structure — what does each directory contain, and is more
>    than one application present?
> 2. Data models — what entities exist, with fields and relationships. Distinguish
>    entities that are *declared* from entities that are *persisted*.
> 3. API routes — method, path, and what each does.
> 4. Authentication — how does the API establish who is making a request?
> 5. Database — which engine, how is the connection configured, is there a
>    migration system and what revision is current?

Two deliberate properties. Each question is separately answerable, so a wrong
answer is localised rather than smeared through a paragraph. And question 1 asks
whether *more than one* application is present — a question with a yes/no answer
that I could check independently, planted specifically to catch the failure mode
below.

## The answer, and what checking it showed

| Claim | Verified against | Verdict |
|---|---|---|
| Two applications, not one | `ls -d */`, file counts | **True** — 1,446 Python files under `api/` and `worker/`, 4 JavaScript files under `src/` and `tests/` |
| TaskFlow entities: users, teams, invitations | `src/models/store.js` | True as *declared* — three empty in-memory arrays |
| Those entities are persisted | schema, migrations | **False** — nothing persists them; they are module-level arrays that die with the process |
| Routes: `GET /health`, `GET /teams` | `src/routes/index.js` | True but misleading — `listRoutes()` returns two **strings**. No HTTP server exists and no handler is bound |
| Auth mechanism | grep across `src/` | **None** — 0 files mention auth, token or session |
| Database: Postgres, alembic at 0005 | `api/alembic/versions/` | True *of the Python app*, false of TaskFlow — 0 files in `src/` reference a database |

## The finding this exercise was actually for

The instruction is "summarize this codebase", and this directory contains two
unrelated codebases: a mature Python service with row-level security, five
migrations and four standing controls, and a four-file JavaScript scaffold with
no server, no persistence and no auth.

An agent asked to summarise "the codebase" will produce one coherent description,
because coherence is what it is optimising for. The likely output is a TaskFlow
API that uses Postgres, has migrations, and enforces tenant isolation — every
component of which is true of *something in this directory* and none of which is
true of TaskFlow. That is the plausible-and-wrong failure in its purest form:
not a hallucination, a **misattribution**. Every fact is real; the subject is wrong.

It is also not the agent's mistake. The question presupposed one application.

## What made the answer checkable

The trust did not come from the answer's confidence or detail. It came from
asking questions whose answers are countable — how many files, which revision,
does any file mention auth — so verification is a command rather than a judgment.

The two claims that would have slipped past a careless reader are the ones stated
in *true-but-misleading* form: "routes exist" (they are strings in an array) and
"entities exist" (they are empty arrays). Both are literally accurate. Both would
lead someone to build on a foundation that is not there. The lesson I take is that
"is this true" is the weaker question; "is this true **of the thing I am about to
build on**" is the one that catches this class.
