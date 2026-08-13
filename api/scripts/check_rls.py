"""CLI wrapper over app.rls -- assert the live RLS configuration matches the migrations.

The judgment lives in app/rls.py so that this script, the boot guard in app.db,
and CI cannot drift apart from each other. This file owns only the two things a
CLI owns: which connection to use, and how to report.

Connects as the *migration* role deliberately. The boot guard already covers the
app role's view; running the same assertion through the owner is what makes the
FORCE check meaningful, since an unforced policy is invisible from the owner's
side precisely because the owner is bypassing it.

Intended call sites:
    .venv/bin/python scripts/check_rls.py      # by hand, after any schema work
    CI, as a step immediately after `alembic upgrade head`

Exit 0 = live schema matches the migrations. Exit 1 = drift.
"""

import sys
from pathlib import Path

from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.rls import TABLES, connecting_role, find_drift  # noqa: E402


def main() -> int:
    app_role = connecting_role(settings.DATABASE_URL)
    engine = create_engine(str(settings.MIGRATION_DATABASE_URL))

    with engine.connect() as conn:
        problems = find_drift(conn, app_role=app_role)

    if problems:
        # stderr, and one problem per line: this runs in CI, where the failing
        # line is the whole message and a single wrapped paragraph is unreadable.
        print("RLS DRIFT -- live schema does not match the migrations:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        # Deliberately not "run alembic upgrade head" flat. That re-asserts the
        # policies and fixes nothing for a stray GRANT, and a remediation line
        # that does not work is worse than none -- it costs an engineer the time
        # to try it before they start actually thinking.
        print(
            "  fix: `alembic upgrade head` re-asserts the policies. If it reports nothing "
            "to do, or the line above is a grant, the change was made by hand -- no "
            "migration describes it, so revert it at the source.",
            file=sys.stderr,
        )
        return 1

    print(
        f"RLS ok: {', '.join(TABLES)} -- enabled, forced, USING and WITH CHECK both scoped; "
        f"no partition reachable by {app_role} without RLS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
