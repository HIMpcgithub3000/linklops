"""Regression control: a refusal path must not write contact data to the log.

Fourth control in the family with check_rls.py, check_url_policy.py and
check_log_injection.py, and the natural pair to the last of those. That one
asserts a log record cannot become two lines -- structure. This one asserts a
log record cannot become a copy of somebody's address book -- content.

The invariant: for every path that refuses a request, the records the app emits
may carry a fixed reason and opaque identifiers, and may not carry an email
address or any fragment of one.

Two detectors, because either alone is a hole:

  sentinels   The addresses this script feeds in are unique per run. Any
              emitted record containing one -- whole, local part only, domain
              only, or case-shifted -- is caller-supplied contact data that
              reached a log line, however it was reshaped on the way. A generic
              pattern misses `priya.nair` logged without its domain; a sentinel
              does not.

  pattern     A generic address shape catches PII that never passed through
              this script's arguments -- a row read back from the database, a
              driver message quoting a parameter, an exception rendering a
              model. Sentinels cannot see those, because we never supplied them.

Scope, stated so the exit code is not read as more than it is: this control
watches the invitation refusal paths, which are where addresses live today. It
is not a proof that the service logs no PII anywhere. Adding a path here is the
cost of adding a path that handles contact data.

Exit 0 = every scenario refused, and no emitted record carried an address.
Exit 1 = at least one did, or a scenario stopped refusing.

Requires the database (the paths under test read real rows). Writes nothing:
every scenario runs inside a transaction that is rolled back.
"""

import asyncio
import logging
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.logging_config import JsonFormatter  # noqa: E402
from app.main import database_error  # noqa: E402
from app.services import teams_service as svc  # noqa: E402

# Distinctive enough that a match cannot be coincidence, and shaped like real
# addresses so normalisation treats them normally.
RUN = uuid.uuid4().hex[:8]
INVITED = f"priya.nair.{RUN}@northwind-legal.example"
STRANGER = f"m.webb.{RUN}@contractor-partner.example"

EMAIL_SHAPE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class Capture(logging.Handler):
    """Every record the app emits during a scenario, rendered exactly as the
    service would write it. Rendered, not inspected as a dict: the question is
    what lands in the log file, and the formatter is what decides that."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.formatter = JsonFormatter()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def fragments(address: str) -> list[str]:
    """The address and the pieces it survives as. Logging the local part alone
    still identifies a person inside a tenant; logging the domain alone still
    tells a reader which company was invited."""
    local, _, domain = address.partition("@")
    return [address, local, domain]


def _seed(db: Session) -> tuple[uuid.UUID, uuid.UUID]:
    tenant, team, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # Bind the tenant for the transaction. Migration 0012 put FORCE row-level
    # security on teams/team_memberships/invitations, so seeding them now has to
    # satisfy the WITH CHECK predicate, exactly as a real request does.
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
    db.execute(
        text("INSERT INTO teams (id, tenant_id, name) VALUES (:i,:t,'Control')"),
        {"i": str(team), "t": str(tenant)},
    )
    db.execute(
        text("INSERT INTO team_memberships (team_id,user_id,role) VALUES (:t,:u,'admin')"),
        {"t": str(team), "u": str(admin)},
    )
    return team, admin


def _age_out(db: Session, token: str) -> None:
    db.execute(
        text("UPDATE invitations SET expires_at = :e WHERE token_hash = :h"),
        {"e": datetime.now(UTC) - timedelta(days=1), "h": svc.hash_token(token)},
    )


def _revoke(db: Session, token: str) -> None:
    # revoked_at and revoked_by are paired by ck_invitations_revoked_pair -- the
    # row must be able to answer who revoked it, so half a revocation is not a
    # state the table allows.
    db.execute(
        text("UPDATE invitations SET revoked_at = now(), revoked_by = :b WHERE token_hash = :h"),
        {"b": str(uuid.uuid4()), "h": svc.hash_token(token)},
    )


# Each scenario drives one refusal path and must raise HTTPException.
def scenario_wrong_recipient(db: Session) -> None:
    team, admin = _seed(db)
    _, token = svc.create_invitation(db, team, admin, INVITED, "member")
    svc.accept_invitation(db, token, uuid.uuid4(), STRANGER)


def scenario_unknown_token(db: Session) -> None:
    _seed(db)
    svc.accept_invitation(db, "not-a-real-token", uuid.uuid4(), STRANGER)


def scenario_expired(db: Session) -> None:
    team, admin = _seed(db)
    _, token = svc.create_invitation(db, team, admin, INVITED, "member")
    _age_out(db, token)
    svc.accept_invitation(db, token, uuid.uuid4(), INVITED)


def scenario_revoked(db: Session) -> None:
    team, admin = _seed(db)
    _, token = svc.create_invitation(db, team, admin, INVITED, "member")
    _revoke(db, token)
    svc.accept_invitation(db, token, uuid.uuid4(), INVITED)


def scenario_invalid_role(db: Session) -> None:
    """The email is supplied and the request is refused before it is stored --
    the shape where an argument is most likely to be logged 'for context'."""
    team, admin = _seed(db)
    svc.create_invitation(db, team, admin, INVITED, "viewer")


def scenario_not_an_admin(db: Session) -> None:
    team, _ = _seed(db)
    svc.create_invitation(db, team, uuid.uuid4(), INVITED, "member")


SCENARIOS = [
    ("accept: forwarded link, wrong recipient", scenario_wrong_recipient),
    ("accept: token that never existed", scenario_unknown_token),
    ("accept: expired invitation", scenario_expired),
    ("accept: revoked invitation", scenario_revoked),
    ("create: role outside the allowed set", scenario_invalid_role),
    ("create: caller is not an admin", scenario_not_an_admin),
]


def provoke_database_error(db: Session) -> DBAPIError:
    """A real constraint violation on a row that holds an address.

    Not a hand-built exception: the leak this guards against lives in what the
    driver puts in its message, so a synthetic error would test nothing. The
    violation is the revoked_at/revoked_by pair, chosen because it fires on
    INSERT and so makes Postgres quote the whole proposed row back.
    """
    team, tenant, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
    db.execute(
        text("INSERT INTO teams (id, tenant_id, name) VALUES (:i,:t,'Control')"),
        {"i": str(team), "t": str(tenant)},
    )
    try:
        db.execute(
            text(
                """
                INSERT INTO invitations (id, team_id, email, role, token_hash,
                                         invited_by, expires_at, revoked_at)
                VALUES (:i, :t, :e, 'member', :h, :b,
                        now() + interval '7 days', now())
                """
            ),
            {"i": str(uuid.uuid4()), "t": str(team), "e": INVITED,
             "h": "0" * 64, "b": str(admin)},
        )
    except DBAPIError as exc:
        return exc
    raise AssertionError("expected the CHECK constraint to fire")


def scan(lines: list[str], label: str) -> list[str]:
    """Both detectors over every line one scenario emitted."""
    found: list[str] = []
    for line in lines:
        haystack = line.lower()
        hits = [
            fragment
            for address in (INVITED, STRANGER)
            for fragment in fragments(address)
            if fragment.lower() in haystack
        ]
        if hits:
            found.append(
                f"  logged caller-supplied contact data {sorted(set(hits))[0]!r}: {label}"
            )
            continue
        shaped = EMAIL_SHAPE.findall(line)
        if shaped:
            found.append(f"  logged an address-shaped value {shaped[0]!r}: {label}")
    return found


def main() -> int:
    engine = create_engine(str(settings.DATABASE_URL))
    capture = Capture()
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    root.handlers = [capture]
    root.setLevel(logging.DEBUG)

    failures: list[str] = []
    try:
        for label, scenario in SCENARIOS:
            capture.lines.clear()
            conn = engine.connect()
            tx = conn.begin()
            db = Session(bind=conn, join_transaction_mode="create_savepoint")
            refused = False
            try:
                scenario(db)
            except HTTPException:
                refused = True
            finally:
                db.close()
                tx.rollback()
                conn.close()

            if not refused:
                # A scenario that stops refusing is a hole in this control, not
                # a pass: it would report clean logs for a path it never drove.
                failures.append(f"  did not refuse, so nothing was proven: {label}")
                continue

            failures.extend(scan(capture.lines, label))

        # The same invariant one layer up. app/main.py answers a driver error
        # with an opaque 500 and puts the cause in the log -- which is right,
        # and is exactly why what it writes there is worth watching: a psycopg
        # constraint message quotes the whole failing row.
        capture.lines.clear()
        conn = engine.connect()
        tx = conn.begin()
        db = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            exc = provoke_database_error(db)
        finally:
            db.close()
            tx.rollback()
            conn.close()

        label = "handler: driver error on a table holding addresses"
        response = asyncio.run(database_error(None, exc))
        if response.status_code != 500:
            failures.append(f"  did not answer 500, so nothing was proven: {label}")
        else:
            failures.extend(scan(capture.lines, label))
            if not capture.lines:
                failures.append(f"  wrote no log line at all, losing the cause: {label}")
    finally:
        root.handlers, root.level = previous_handlers, previous_level

    if failures:
        print("check_log_pii: FAIL")
        for failure in failures:
            print(failure)
        return 1

    print(
        f"check_log_pii: OK ({len(SCENARIOS)} refusal paths + the driver-error "
        "handler, no address reached a log line)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
