"""T2 — role enforcement primitive, and T4/T5 invitation logic.

The one rule the whole module hangs on, settled in the Module 02 decision layer:
**authority is a property of now, not of when the token was minted.** Every
authorisation check runs at the moment of the action against current state,
never against a snapshot taken at send time.
"""

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# owner > admin > member. Comparison is by rank, so "at least admin" is one
# expression rather than a set literal repeated at every call site.
ROLE_RANK = {"member": 1, "admin": 2, "owner": 3}
INVITE_TTL = timedelta(days=7)


def hash_token(token: str) -> str:
    """sha256, matching the api_keys treatment. An invitation token is a bearer
    credential -- it grants team membership to whoever holds it -- so it is
    stored the way a password is, not the way an id is."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_email(email: str) -> str:
    """The uniqueness rule and the redemption rule must agree on what "the same
    email" means, or an invitation sent to Foo@Bar.com cannot be redeemed by
    foo@bar.com. Normalise once, here, and use it for both."""
    return email.strip().lower()


def require_role(db: Session, team_id: uuid.UUID, user_id: uuid.UUID, minimum: str) -> str:
    """Resolve the caller's role for a team and refuse by default.

    Default-deny is the point: a caller with no membership row gets 404, not
    403. A 403 confirms the team exists, which turns this endpoint into a team
    enumeration oracle for anyone with an account.

    Raises 404 for both "no such team" and "not a member", deliberately
    identically, so the two cases are indistinguishable from outside.
    """
    row = db.execute(
        text("SELECT role FROM team_memberships WHERE team_id = :t AND user_id = :u"),
        {"t": str(team_id), "u": str(user_id)},
    ).first()

    if row is None or ROLE_RANK[row.role] < ROLE_RANK[minimum]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TEAM_NOT_FOUND", "message": "Team not found."},
        )
    return row.role


def create_invitation(
    db: Session, team_id: uuid.UUID, actor_id: uuid.UUID, email: str, role: str
) -> tuple[dict, str]:
    """T4. Returns (invitation_row, plaintext_token).

    The plaintext token exists here and nowhere else. It goes to the caller and
    to the email; it is never recoverable from the database.
    """
    require_role(db, team_id, actor_id, "admin")

    if role not in ROLE_RANK:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_ROLE", "message": "Role must be owner, admin or member."},
        )

    email = normalise_email(email)
    token = secrets.token_urlsafe(32)

    try:
        row = db.execute(
            text(
                """
                INSERT INTO invitations
                    (id, team_id, email, role, token_hash, invited_by, expires_at)
                VALUES (:id, :team, :email, :role, :hash, :by, :exp)
                RETURNING id, team_id, email, role, expires_at, created_at
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "team": str(team_id),
                "email": email,
                "role": role,
                "hash": hash_token(token),
                "by": str(actor_id),
                "exp": datetime.now(UTC) + INVITE_TTL,
            },
        ).one()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_invitations_live_email" not in str(exc.orig):
            raise
        # Re-inviting the same email returns the SAME invitation, not a second
        # one -- the acceptance criterion from T4. The token is NOT re-issued,
        # because we do not have it: only its hash was stored. The caller is
        # told the invitation already exists rather than being handed a token
        # that would not work.
        existing = db.execute(
            text(
                """
                SELECT id, team_id, email, role, expires_at, created_at
                FROM invitations
                WHERE team_id = :team AND lower(email) = :email
                  AND revoked_at IS NULL AND accepted_at IS NULL
                """
            ),
            {"team": str(team_id), "email": email},
        ).one()
        return dict(existing._mapping), ""

    return dict(row._mapping), token


def accept_invitation(db: Session, token: str, user_id: uuid.UUID, user_email: str) -> dict:
    """T5. Authority is a property of now.

    Every condition below is evaluated against current state at the moment of
    acceptance -- not against anything decided when the token was minted.
    """
    row = db.execute(
        text(
            """
            SELECT id, team_id, email, role, expires_at, revoked_at, accepted_at
            FROM invitations WHERE token_hash = :h
            """
        ),
        {"h": hash_token(token)},
    ).first()

    # One identical 404 for: no such token, expired, revoked, already accepted,
    # and wrong recipient. Distinguishing them tells a holder of a dead token
    # that it was once real, and tells a stranger that an invitation exists for
    # an address they guessed.
    def dead(reason: str) -> HTTPException:
        # Support needs to tell an expiry apart from a wrong recipient; the
        # caller must not be able to. So the discriminator goes to the log and
        # the response stays identical for all five causes.
        #
        # What may go in that line is the constraint that matters. `reason` is
        # chosen from a fixed set in this function -- it is not derived from
        # anything the caller sent -- and the two ids are opaque handles that
        # resolve to a person only through an authenticated admin lookup. Email
        # addresses are the opposite on both counts: `invited_email` is contact
        # data for a third party who is not even the caller, and logging it here
        # reopens the enumeration oracle the identical-404 above exists to
        # close. Anyone who can POST a guessed token gets nothing back, but the
        # log line records the address the token belongs to -- and this log
        # stream is exported to customers, so "internal only" is not true of it.
        #
        # INFO, not WARNING, for the same reason a 404 on an unknown short code
        # is INFO (see app/logging_config.py): one dead token is the service
        # working correctly. The alertable signal is the rate, not the line.
        log.info(
            "invitation rejected",
            extra={
                "reason": reason,
                "invitation_id": str(row.id) if row is not None else None,
                "user_id": str(user_id),
            },
        )
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INVITATION_NOT_FOUND", "message": "This invitation is not valid."},
        )

    if row is None:
        raise dead("no_such_token")
    if row.revoked_at is not None or row.accepted_at is not None:
        raise dead("revoked_or_already_accepted")
    if row.expires_at <= datetime.now(UTC):
        # Read-time evaluation, per the decision layer: no job flips a status
        # column, so there is no window in which an expired invitation resolves.
        raise dead("expired")
    if normalise_email(user_email) != normalise_email(row.email):
        # The token alone is not sufficient. A forwarded link does not grant
        # membership to whoever opens it.
        raise dead("recipient_mismatch")

    # Single-use is enforced by the WHERE clause, not by the check above: two
    # concurrent accepts both pass the checks, and exactly one updates a row.
    claimed = db.execute(
        text(
            """
            UPDATE invitations SET accepted_at = now(), accepted_by = :u
            WHERE id = :id AND accepted_at IS NULL AND revoked_at IS NULL
            RETURNING id
            """
        ),
        {"u": str(user_id), "id": str(row.id)},
    ).first()
    if claimed is None:
        db.rollback()
        raise dead("lost_concurrent_claim")

    db.execute(
        text(
            """
            INSERT INTO team_memberships (team_id, user_id, role)
            VALUES (:t, :u, :r)
            ON CONFLICT (team_id, user_id) DO NOTHING
            """
        ),
        {"t": str(row.team_id), "u": str(user_id), "r": row.role},
    )
    db.commit()
    return {"team_id": row.team_id, "role": row.role}


def revoke_invitation(
    db: Session, team_id: uuid.UUID, invitation_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """T6. A state transition, never a delete -- the row must be able to answer
    who revoked it and when."""
    require_role(db, team_id, actor_id, "admin")
    result = db.execute(
        text(
            """
            UPDATE invitations SET revoked_at = now(), revoked_by = :by
            WHERE id = :id AND team_id = :t AND revoked_at IS NULL AND accepted_at IS NULL
            """
        ),
        {"by": str(actor_id), "id": str(invitation_id), "t": str(team_id)},
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INVITATION_NOT_FOUND", "message": "This invitation is not valid."},
        )
