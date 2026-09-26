# Reply to the PM — Monday 09:10

*(Sent Monday morning, not Friday evening. Acknowledged receipt Friday with
"got it, I'll come back Monday with options and costs" and nothing else — a
4:47 PM Friday message is the worst moment for judgment and the best moment for
capitulation.)*

---

**Yes — we can show money moving on Friday.** Here's how I'd do it, and the one
thing I need from you.

**Recommendation: two demos side by side, not one combined one.**

1. **The booking flow** (Slice 1, already planned) — browse, pick a slot, book,
   confirmation. Real, persisted, working.
2. **A payment proof page** (new, half a day) — Stripe test mode, one amount, a
   card field, a real charge landing in the Stripe dashboard while they watch.

That's half a day of extra work. Slice 1 slips from Tuesday to Wednesday, which
still leaves Thursday for a dry run.

**Why not just put payments inside the booking flow?**

The main reason is not the cost — it's that **both versions show test-mode
money.** Neither one is a real charge. So on the question the investor actually
raised — can this platform take money — the integrated version and the standalone
version give them exactly the same evidence, and the integrated version costs
about four times as much.

The second reason is what happens if it breaks. Payments is our one external
integration and we've never wired it up before. If the two demos are separate and
payments has a bad day, we show the booking flow and say the payment work is
mid-flight. If they're combined and it breaks, **it breaks live, at the payment
step, in front of the person whose stated concern is that we can't handle
money.** That doesn't just cost us the demo — it demonstrates the thing they were
worried about.

And a smaller one: Slice 1 exists to tell us whether people book by choosing a
*person* or by choosing a *time*. If we ask for a card in the same flow, every
hesitation becomes ambiguous — confusing UI, or reluctance to enter a card? We'd
lose that answer to buy evidence we can get anyway.

**What I'm explicitly not claiming.** The payment page proves money can move
through an account we control. It does **not** prove our booking model works with
Stripe — the hard part is holding an authorization when someone books and
capturing it when the job is done, plus webhooks and refunds. None of that is in
the half-day version. I'd rather you have that sentence now than have it come up
in Q&A.

**If integrated payment is genuinely non-negotiable**, here's the trade I'd
make — your call, not mine:

- Cut Slice 1 down to **one provider, one service, three slots**, no list page.
  The demo becomes: pick a slot → pay → confirmation, end to end and real.
- Cost: ~3 days instead of ~1.5, and we lose the "does this feel like a
  marketplace" read and the book-by-person-vs-by-time question entirely. The
  first slice stops teaching us anything about navigation.
- I'd take that trade if the investor conversation is worth more than the product
  learning this month. That's a legitimate call — I just want it made on purpose.

**What I need from you: one answer.** When the investor said the demo would be
"concerning" without payments — were they asking *"is there a business here,
does money ever change hands"*, or *"show me the actual commercial flow working
end to end"*? The first is answered by the half-day page. The second isn't, and
if it's the second I need to know today, not Thursday.

**One correction on the date:** Friday is the demo, so content freezes Wednesday
— we need Thursday for a dry run on the real machine. Real budget is three
working days, not five.
