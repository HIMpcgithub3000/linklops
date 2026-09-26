#!/usr/bin/env python
"""Catastrophic-backtracking budget for the destination validator.

The validator is currently immune by construction: it parses with urlsplit and
caps length at MAX_URL_LENGTH, and there is no regex on the matching path. That
is a property of today's implementation, not a guarantee -- someone adds one
`re.match` for "just a quick check on the path segment" and the immunity is gone
with nothing to notice.

So this asserts the OBSERVABLE property rather than the implementation: time is
bounded, and it does not grow superlinearly with adversarial input. A regex with
nested quantifiers cannot pass this, whatever it is spelled like.

Adversarial shapes, not random strings. The trailing character that FAILS the
match is what forces the engine to re-partition every prefix -- a long run of
repeats that matches cleanly is cheap.
"""
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.url_policy import validate_destination  # noqa: E402

BUDGET_MS = 50.0        # any single call
GROWTH_MAX = 4.0        # 8x the input may not cost more than 4x the time

SHAPES = [
    ("repeat+bang",  lambda n: "http://example.com/" + "a" * n + "!"),
    ("dots",         lambda n: "http://" + "a." * n + "!"),
    ("dashes-host",  lambda n: "http://" + "a-" * n + "a!"),
    ("slashes",      lambda n: "http://example.com" + "/a" * n + "!"),
    ("query-pairs",  lambda n: "http://example.com/?" + "a=1&" * n + "!"),
]


HARD_TIMEOUT_S = 2            # see the note below -- this is the load-bearing part


class Hung(Exception):
    pass


def timed(url: str) -> float:
    """Returns elapsed ms, or raises Hung.

    The alarm is not belt-and-braces. The first version of this check measured
    duration and nothing else, and when I reintroduced a vulnerable regex to
    watch it fail, THE CHECK HUNG TOO -- because measuring how long a hang takes
    takes exactly as long as the hang. A budget assertion cannot catch the
    failure mode it exists for unless something bounds it from outside.
    So: fail closed on the alarm, never fail open by never returning.
    """
    def _boom(signum, frame):
        raise Hung()

    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, HARD_TIMEOUT_S)
    t = time.perf_counter()
    try:
        validate_destination(url)
    except Hung:
        raise
    except Exception:
        pass                      # a rejection is a fine outcome; a HANG is not
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    return (time.perf_counter() - t) * 1000


def main() -> int:
    failures = []
    for name, build in SHAPES:
        try:
            small, large = timed(build(25)), timed(build(200))
        except Hung:
            print(f"  {name:<14} HUNG past {HARD_TIMEOUT_S}s -- catastrophic backtracking")
            failures.append(f"  {name}: did not return within {HARD_TIMEOUT_S}s")
            continue
        growth = large / small if small > 0.0005 else 1.0
        status = "ok"
        if large > BUDGET_MS:
            failures.append(f"  {name}: {large:.1f} ms exceeds the {BUDGET_MS} ms budget")
            status = "FAIL"
        elif growth > GROWTH_MAX:
            failures.append(
                f"  {name}: 8x input cost {growth:.1f}x time (max {GROWTH_MAX}x) "
                f"-- superlinear, the backtracking signature"
            )
            status = "FAIL"
        print(f"  {name:<14} n=25 {small:7.3f} ms   n=200 {large:7.3f} ms   "
              f"growth {growth:5.2f}x   {status}")

    if failures:
        print("\nFAIL -- possible catastrophic backtracking:")
        print("\n".join(failures))
        print("\nFix order: replace the regex with a parser; else remove the nested\n"
              "quantifier; a length cap alone is defence in depth, not a fix.")
        return 1
    print(f"\nOK -- all shapes bounded under {BUDGET_MS} ms and sublinear-to-linear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
