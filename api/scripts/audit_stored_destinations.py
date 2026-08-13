"""Re-validate every stored destination against the *current* policy.

Tightening validation fixes writes from now on. It does nothing to rows already
in the table, and those rows are the ones an attacker created on purpose while
the hole was open -- so a create-path fix that ships without this leaves the
exploited links working and the incident open. Two halves, and only one of them
is code: stop accepting it, then go find what was already accepted.

This also covers the ordinary case where nobody attacked anything: the policy
gets stricter over time, so rows written under an older, laxer version are
normal, and knowing which ones exist is the difference between a clean tightening
and a silent tail of grandfathered exceptions.

Reads as the owner because it must see every tenant's rows. Disabling sets
disabled_at rather than deleting: resolve_link() already refuses a disabled row,
the code stays claimed so it cannot be reissued to someone else, and the row
survives for whoever has to explain the incident afterwards.

    python scripts/audit_stored_destinations.py            # report only
    python scripts/audit_stored_destinations.py --disable  # report, then disable

Exit 0 = every stored destination still passes. Exit 1 = at least one does not.
"""

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.url_policy import DestinationRejected, validate_destination  # noqa: E402


def main() -> int:
    disable = "--disable" in sys.argv[1:]
    engine = create_engine(str(settings.MIGRATION_DATABASE_URL))

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, tenant_id, code, long_url, disabled_at "
                "FROM links ORDER BY created_at"
            )
        ).all()

        offenders = []
        for row in rows:
            try:
                validate_destination(row.long_url)
            except DestinationRejected as exc:
                offenders.append((row, str(exc)))

        if not offenders:
            print(f"stored destinations ok: {len(rows)} links, all pass the current policy")
            return 0

        live = [r for r, _ in offenders if r.disabled_at is None]
        print(
            f"STORED DESTINATIONS FAIL THE CURRENT POLICY: "
            f"{len(offenders)} of {len(rows)} links ({len(live)} still resolvable)",
            file=sys.stderr,
        )
        for row, reason in offenders:
            state = "already disabled" if row.disabled_at else "LIVE"
            print(f"  [{state}] {row.code}  tenant={row.tenant_id}", file=sys.stderr)
            print(f"      {row.long_url}", file=sys.stderr)
            print(f"      {reason}", file=sys.stderr)

        if disable and live:
            conn.execute(
                text("UPDATE links SET disabled_at = now() WHERE id = ANY(:ids)"),
                {"ids": [row.id for row in live]},
            )
            print(
                f"\n  disabled {len(live)} link(s); resolve_link() now refuses them",
                file=sys.stderr,
            )
            return 0
        if live:
            print("\n  re-run with --disable to stop these resolving", file=sys.stderr)
            return 1

        # Offenders exist but none of them resolve, so there is nothing to act on.
        # Exit 0 deliberately: a remediated row stays on the report forever as
        # history, and a control that is permanently red is a control everyone
        # learns to scroll past. The alert has to clear when the risk clears, or
        # it stops being an alert. Same reasoning as not putting the RLS check on
        # /ready in Module 02 -- a signal nobody can action is noise wearing an
        # alert's clothes.
        print(
            f"\n  none of these resolve; {len(offenders)} already disabled, nothing to do",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
