# Technical Explanation: REST-to-GraphQL Migration

## Decision Summary

**We are replacing our REST API with GraphQL over the next 8 weeks, to stop
building one-off endpoints for every screen.**

Today the backend decides what data to send. After the migration the app asks for
exactly what it needs. REST is a fixed-menu restaurant — you order dish #7 and
get whatever is in dish #7. GraphQL is a buffet — you take only what you want.

Both run side by side during the migration, so nothing breaks while we move.

## Why We Chose This

**The problem is 15 endpoints that exist only because two apps disagree.**

An *endpoint* is one URL the app calls to get one kind of data. We have 47. Of
those, 15 exist solely because the mobile app needs a different shape of the same
data than the web app does — same screen, same information, two endpoints.

**The cost is a third of every sprint.**

Frontend developers spend roughly 30% of each sprint building *aggregation
endpoints* — endpoints whose only job is stitching several services' data into
the shape one screen wants. That work produces no new features. It exists because
the server, not the client, chooses the shape.

**We expect to remove that work, not reduce it.**

Around 40% fewer API calls from the frontend, because one GraphQL query replaces
several REST calls. The 15 duplicate endpoints go away entirely. Mobile ships
features without waiting on a backend endpoint first.

**We rejected two cheaper alternatives, for one reason each.**

- *Backend-for-Frontend* — a translation layer per app. Would have worked, and
  adds a service to deploy, monitor and page someone about. We chose not to buy a
  new operational surface.
- *Standardising REST endpoint shapes* — would not have helped. The apps
  genuinely need different data; a shared schema makes disagreement uniform
  rather than removing it.

## Risks and Mitigations

**Two of the three engineers have never used GraphQL.** Mitigation: they are on
the migration full-time for 8 weeks, which is learning time rather than a tax on
top of feature work.

**Query performance is unpredictable.** A client can request deeply nested data
and generate an expensive query the server never anticipated. REST could not do
this — the shape was fixed, so the cost was known. Mitigation: limit query depth
and complexity before we take external traffic.

**Caching gets harder.** REST responses live at fixed URLs, so any cache can
store them. GraphQL sends POST requests carrying a query, which ordinary caches
cannot key on. Mitigation: caching moves inside the application rather than
sitting in front of it — this is a real capability we are giving up, not a
detail.

## What This Means for Priya

**You are arriving mid-migration, and that is the most useful thing to know.**

Both APIs are live. When you touch an endpoint, check which one the caller is on
— REST or GraphQL — because the answer changes what "done" means.

**Do not add REST endpoints.** If a screen needs a new shape, that is a GraphQL
query now. Adding a 48th endpoint recreates the problem we are 8 weeks into
removing.

**You do not need to know GraphQL on day one.** Two of the three engineers on it
did not either. There is more unfamiliarity on this team than familiarity, which
makes questions normal rather than expensive.

**The decision is settled; the design is not.** Query depth limits and the
caching approach are open. Those are worth your opinion, and you have the
advantage of not having been in the room when the rest was decided.

## Next Steps

1. **Week 1 (you):** read one existing GraphQL query and its REST equivalent,
   side by side. Fastest way to see the difference.
2. **Before external traffic:** query depth and complexity limits — currently
   unmitigated and the risk most likely to cause an incident.
3. **Ongoing:** as each of the 15 aggregation endpoints loses its last caller,
   delete it. The migration is only finished when they are gone; running both
   forever is the failure mode.

---

## Appendix — rewriting the ShopStream memo (Module 02 FIX)

Same facts, same decision, restructured.

> # API migration: REST → GraphQL, starting Monday
>
> **We are migrating our API from REST to GraphQL over the next 8 weeks.** Three
> engineers full-time. Both APIs run side by side, so nothing breaks during the
> move.
>
> ## Why
>
> Frontend developers — the team building what users see — spend **30% of every
> sprint** writing *aggregation endpoints*: URLs whose only job is reshaping data
> from several services to fit one screen. That work ships no features.
>
> The cause is that our web and mobile apps need different shapes of the same
> data, so we build a separate endpoint for each. Fifteen of our 47 endpoints
> exist only for that reason.
>
> GraphQL lets the client ask for exactly the data it needs, so one query
> replaces several calls. REST is a fixed menu; GraphQL is a buffet.
>
> ## What we expect
>
> - ~40% fewer frontend API calls
> - 15 aggregation endpoints deleted
> - Mobile ships features without waiting on a new endpoint
>
> ## What we rejected, and why
>
> - **Backend-for-Frontend** (a translation layer per app): would have worked. We
>   chose not to add a service to deploy, monitor and page someone about.
> - **Standardising REST shapes**: would not have helped — the apps genuinely
>   need different data.
>
> ## Risks
>
> - **Two of the three engineers have not used GraphQL.** Mitigated by pair
>   programming and dedicated learning time in weeks 1–2.
> - **Query cost is unpredictable** — a client can request deeply nested data and
>   generate an expensive query. Depth limits before external traffic.
> - **Caching gets harder.** REST responses sit at fixed URLs any cache can
>   store; GraphQL POSTs a query body that ordinary caches cannot key on. This is
>   a capability we are giving up, not a detail.
>
> Reliability (SRE) measured the mobile app making several sequential REST calls
> where one query does the job. The SDK team's versioning concerns get easier:
> fewer endpoints, fewer compatibility surfaces.

### What changed

| Problem | Original | Rewrite |
|---|---|---|
| Buried lede | decision in ¶4 | first sentence |
| Structure | no headings, 4 × ~8-sentence blocks | 5 headings, skimmable |
| Passive voice | "a decision has been reached", "were deemed" | "we are migrating", "we chose not to" |
| Jargon | FE, BFF, SDK, SRE undefined | defined at first use, then used freely |
| Wordiness | "It should be noted that" ×2, 22-word factor list | cut |
| Ambiguity | "significant sprint capacity" | **30%** |
| Ambiguity | "could theoretically suffice" | "one query replaces several calls" |

**The counter-intuitive part:** the original contains *more* facts than my
version — SDK versioning, SRE latency work — and communicates less, because
nothing is ranked. The rewrite keeps those facts and demotes them to a closing
paragraph. Adding information is not adding clarity; the reader still has to be
told which fact they must act on.
