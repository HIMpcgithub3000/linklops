# Making checkout hold up under peak traffic — a Q3 plan and one decision

**To:** Sarah (VP Engineering), David (Head of Product)
**From:** Marcus, Platform
**Decision needed:** before we commit Q3 engineering time

---

**Last Black Friday, our checkout system slowed to a near-stop for 47 minutes at
peak — customers who were ready to pay couldn't.** The system that routes all our
traffic ran out of room to handle the load. We have a plan to replace it with a
more reliable one this quarter, and there's one risk I need to put in front of
you before we start, because it involves Black Friday again.

## What's wrong today

Think of our traffic system as the front door and hallways of a building that
every customer walks through to reach any part of our product. The current one
is old, no longer getting safety updates, and — as last November showed — can't
grow fast enough when a crowd arrives all at once. When it got overwhelmed,
checkout was the room that failed.

## What we want to do

Replace that front-door-and-hallways system with a newer one that handles crowds
better and is safer by default. The work is mostly rebuilding some of our own
internal tools to fit the new system — that rebuild is the bulk of the timeline.

- **Effort:** 2 engineers, about 8 weeks.
- **Customer impact during the work:** none planned. We run the old and new
  systems side by side and only switch once the new one has proven itself, and we
  can switch back instantly if it misbehaves.

## The risk you need to weigh

**The new system may need more server capacity than we have today, and we
haven't yet confirmed we have enough for a traffic spike.** If we roll this out
without checking that, and a crowd hits, we could see the same checkout
slowdown that cost us sales last Black Friday — except caused by the very change
meant to prevent it.

This is manageable, but only if we treat it as a gate, not an afterthought: we
test the new system at twice our busiest-ever traffic *before* committing, and we
finish the whole rollout with comfortable margin ahead of Black Friday, not up
against it. If the test doesn't pass, we stay on the current system and
re-plan.

## What I need from you

1. **Sarah — approval to commit 2 engineers for 8 weeks in Q3.** This is time
   that would otherwise go to roadmap work, so it's a real trade, not a free
   win.
2. **David — agreement on timing:** we finish and prove this out with margin
   before the Black Friday run-up. I'd like your line in the sand for "done by"
   so we plan backward from it.
3. **Both — sign-off on the go/no-go rule:** if the capacity test fails, we do
   not cut over. Naming that now means nobody has to make the call under
   pressure later.

Can we take 30 minutes this week to lock these three? Happy to go deeper on any
of the technical detail if useful, but these three decisions are what unblock
the work.
