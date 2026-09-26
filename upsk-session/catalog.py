#!/usr/bin/env python3
"""Pack/module access map across every skill.

~30 lines instead of the 9KB of JSON `upsk catalog --json` returns.

    ./catalog.py           all skills
    ./catalog.py system    only skills matching "system" (case-insensitive)
"""

import json
import subprocess
import sys

LABEL = {"unlocked": "unlocked",
         "awaiting_unlock": "AWAITING UNLOCK",
         "locked": "locked"}
NOTE = {"locked": "   <- needs a seat from your coordinator",
        "awaiting_unlock": "   <- finished, next pack not assigned"}


def main() -> int:
    r = subprocess.run(["upsk", "catalog", "--json"],
                       capture_output=True, text=True)
    out = r.stdout or r.stderr
    try:
        skills = json.loads(out)["data"]["skills"]
    except Exception:
        print(out.strip()[:400], file=sys.stderr)   # CLI error text, not JSON
        return 1

    filt = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    for s in skills:
        if filt and filt not in s["name"].lower() and filt not in s["slug"].lower():
            continue
        prog = s.get("session_progress") or {}
        pct = prog.get("progress_percent")
        done = prog.get("modules_completed", s.get("completed_sessions") or 0)

        head = f"{s['name']}  ({s['modules_count']} modules)"
        if pct is not None:
            head += f"  --  {pct}%, {done} complete"
        if s.get("has_active_session"):
            head += "   *ACTIVE*"
        print(f"\n{head}\n  {s['slug']}")

        for p in s["packs"]:
            m = p["module_numbers"]
            span = f"{m[0]}-{m[-1]}" if len(m) > 1 else str(m[0])
            state = p["access_state"]
            print(f"    {p['name']:<14} modules {span:<6} "
                  f"{LABEL.get(state, state)}{NOTE.get(state, '')}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
