# How I'm taught

Pretest scored ~9/10 across six technical questions. The instructor set calibration
explicitly: skip basic definitions, ask "what breaks in production" after every VERIFY,
select harder BREAK bugs earlier. Recorded `effective_level: advanced`,
`confidence_level: high`.

## Two tracked gaps — work against these actively

1. **I under-weight the time axis.** I reason spatially about current state, less about
   what changed and when. Push toward onset time, correlation IDs, what was true an hour
   ago. Module 02's BREAK proved this live: I had what and why cold, and no answer at all
   for when.
2. **I cost a mechanism correctly but don't always verify the intended benefit lands.**
   I'll argue an index correctly and never check the planner chose it. Make me prove the
   benefit, not just the reasoning.

## Recorded strengths

Unprompted security reasoning. Naming concrete failure modes rather than abstractions.
Framing tradeoffs as purchases with a price.

## Style signals on record

- Responds to adversarial challenge rather than explanation; pushes back with evidence
  when a claim is overstated — including on the instructor's own evaluation, which
  worked in Module 01 and the instructor conceded.
- Verifies empirically rather than reasoning from documentation.

Don't soften. If an answer is wrong, say so and show the counterexample.

## The one growth area on record

Module 01: the validator's error printer echoed the raw error input. A missing-field
error carries the parent object rather than the absent field, so the required-field path
printed the entire collected settings dict to stderr — two database passwords, a cache
password, the token signing key and both tenant API keys — on the path most likely to
fire in a fresh deployment. Fixed, but it's on the record. Treat any error path that
touches config as a redaction question first.

## Dimension scores after Module 01 (all 5.0, insufficient data for trend)

communication_clarity, debugging_methodology, production_thinking, security_awareness,
tradeoff_reasoning.
