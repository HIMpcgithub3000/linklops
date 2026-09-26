# Subscription launch: moving 3 weeks to close a payment security gap

**To:** VP of Product, Head of Marketing
**From:** Payments Engineering
**Date:** 2026-08-18
**Decision needed by:** Thursday

---

**We need to move the subscription launch from March 15 to April 5 — a three-week
delay — to fix a security issue in how we handle credit card payments.** Nothing
has gone wrong for a customer yet. We found this ourselves, before anyone
outside the company could, and we want to close it before we put a new payment
flow in front of more people.

## What we found, in plain terms

When a customer pays, we don't store their real card number. We swap it for a
throwaway code — like a coat-check ticket. You hand over your coat, you get a
stub, and the stub is useless to a thief because only the coat-check desk can
match it back to your coat.

The problem: right now, **a copy of that ticket can be used a second time.**
Someone who managed to grab one customer's payment code could reuse it to make a
charge that customer never approved. No one has done this yet. But when this kind
of weakness has surfaced at other companies, attackers have exploited it within
a few weeks of it becoming known.

**The analogy for the decision in front of us:** launching on the original date
is like opening a bigger store while the back door doesn't lock. The lights are
on, the shelves are full, nobody has tried the handle — but it's only a matter of
time, and the store is now busier than ever.

## Why this pushes the launch

The fix touches three parts of our payment system and takes about three weeks.
It needs the same engineers who are building the subscription service, and they
can't do both at once. Choosing to fix this first is choosing not to launch a new
way to charge customers on top of a known payment hole.

## What this changes

- **Launch date:** March 15 → **April 5**.
- **The subscription service itself is unchanged** — same features, same plan.
  Only the date moves.
- **Marketing:** the announced timeline and any booked promotion need to shift by
  three weeks.
- **Risk of *not* delaying:** shipping a new payment flow while this gap is open,
  during exactly the period this class of issue tends to get exploited.

## What we need from you

1. **Approval to move the launch date to April 5.**
2. **Marketing to hold external announcements** until the new date is confirmed.
3. **30 minutes on Thursday** for the three of us to agree on how we explain the
   new date to customers and the board.

We'd rather have one awkward conversation about a moved date than the far harder
one about unauthorized charges on customer cards. Happy to walk through any of
this live.
