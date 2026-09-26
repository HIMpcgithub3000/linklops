"""Tests for T2-T6. Written negative-first: the positive path was never in danger."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.services import teams_service as svc


def _team(db, tenant):
    # Bind the tenant for the transaction. Migration 0012 put FORCE row-level
    # security on teams/team_memberships/invitations, so an INSERT now has to
    # satisfy the WITH CHECK predicate -- exactly the second wall that finding F2
    # added. Binding here is what a real request does; these tests simply have to
    # do it too now. The binding is transaction-local and persists for the rest
    # of the test, so _member and require_role below run under the same tenant.
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
    tid = uuid.uuid4()
    db.execute(text("INSERT INTO teams (id, tenant_id, name) VALUES (:i,:t,'T')"),
               {"i": str(tid), "t": str(tenant)})
    return tid


def _member(db, team, user, role):
    db.execute(text("INSERT INTO team_memberships (team_id,user_id,role) VALUES (:t,:u,:r)"),
               {"t": str(team), "u": str(user), "r": role})


# --- T2: default-deny ------------------------------------------------------

def test_non_member_gets_404_not_403(db, tenant):
    """404, not 403. A 403 confirms the team exists and turns this into an
    enumeration oracle."""
    team, stranger = _team(db, tenant), uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        svc.require_role(db, team, stranger, "member")
    assert e.value.status_code == 404


def test_nonexistent_team_is_indistinguishable_from_not_a_member(db, tenant):
    team, stranger = _team(db, tenant), uuid.uuid4()
    with pytest.raises(HTTPException) as a:
        svc.require_role(db, team, stranger, "member")
    with pytest.raises(HTTPException) as b:
        svc.require_role(db, uuid.uuid4(), stranger, "member")
    assert a.value.status_code == b.value.status_code
    assert a.value.detail == b.value.detail


def test_member_cannot_act_as_admin(db, tenant):
    team, user = _team(db, tenant), uuid.uuid4()
    _member(db, team, user, "member")
    with pytest.raises(HTTPException) as e:
        svc.require_role(db, team, user, "admin")
    assert e.value.status_code == 404


# --- vocabulary agreement (criterion added in Module 02) -------------------

def test_role_outside_allowed_set_is_refused(db, tenant):
    """'viewer' is a valid string. This negative criterion catches two tasks
    disagreeing about vocabulary."""
    team, admin = _team(db, tenant), uuid.uuid4()
    _member(db, team, admin, "admin")
    with pytest.raises(HTTPException) as e:
        svc.create_invitation(db, team, admin, "x@y.co", "viewer")
    assert e.value.status_code == 400


# --- T4 --------------------------------------------------------------------

def test_reinviting_same_email_returns_same_invitation(db, tenant):
    team, admin = _team(db, tenant), uuid.uuid4()
    _member(db, team, admin, "admin")
    first, tok = svc.create_invitation(db, team, admin, "a@b.co", "member")
    second, tok2 = svc.create_invitation(db, team, admin, "A@B.CO", "member")
    assert first["id"] == second["id"], "different case must be the same address"
    assert tok2 == "", "the original token cannot be reissued; only its hash was stored"


def test_plaintext_token_is_not_in_the_database(db, tenant):
    team, admin = _team(db, tenant), uuid.uuid4()
    _member(db, team, admin, "admin")
    _, token = svc.create_invitation(db, team, admin, "a@b.co", "member")
    hit = db.execute(text("SELECT 1 FROM invitations WHERE token_hash = :t"),
                     {"t": token}).first()
    assert hit is None


# --- T5: authority is a property of now ------------------------------------

def test_forwarded_token_does_not_grant_membership(db, tenant):
    """The token alone is not sufficient -- the email constrains who may redeem."""
    team, admin = _team(db, tenant), uuid.uuid4()
    _member(db, team, admin, "admin")
    _, token = svc.create_invitation(db, team, admin, "invited@b.co", "member")
    with pytest.raises(HTTPException) as e:
        svc.accept_invitation(db, token, uuid.uuid4(), "someone.else@b.co")
    assert e.value.status_code == 404


def test_expired_invitation_is_refused_without_any_job_running(db, tenant):
    team, admin = _team(db, tenant), uuid.uuid4()
    _member(db, team, admin, "admin")
    _, token = svc.create_invitation(db, team, admin, "a@b.co", "member")
    db.execute(text("UPDATE invitations SET expires_at = :p WHERE token_hash = :h"),
               {"p": datetime.now(UTC) - timedelta(seconds=1),
                "h": svc.hash_token(token)})
    with pytest.raises(HTTPException):
        svc.accept_invitation(db, token, uuid.uuid4(), "a@b.co")


def test_revoked_invitation_is_refused_even_though_row_still_exists(db, tenant):
    team, admin = _team(db, tenant), uuid.uuid4()
    _member(db, team, admin, "admin")
    row, token = svc.create_invitation(db, team, admin, "a@b.co", "member")
    svc.revoke_invitation(db, team, row["id"], admin)
    assert db.execute(text("SELECT revoked_by FROM invitations WHERE id=:i"),
                      {"i": str(row["id"])}).scalar() is not None, "audit trail retained"
    with pytest.raises(HTTPException):
        svc.accept_invitation(db, token, uuid.uuid4(), "a@b.co")


def test_invitation_is_single_use(db, tenant):
    team, admin = _team(db, tenant), uuid.uuid4()
    _member(db, team, admin, "admin")
    _, token = svc.create_invitation(db, team, admin, "a@b.co", "member")
    svc.accept_invitation(db, token, uuid.uuid4(), "a@b.co")
    with pytest.raises(HTTPException):
        svc.accept_invitation(db, token, uuid.uuid4(), "a@b.co")


def test_all_dead_token_paths_return_an_identical_body(db, tenant):
    """Unknown and wrong-recipient must be indistinguishable from outside."""
    team, admin = _team(db, tenant), uuid.uuid4()
    _member(db, team, admin, "admin")
    bodies = []
    for setup, email in [("unknown", "a@b.co"), ("wrongrecipient", "z@z.co")]:
        _, token = svc.create_invitation(db, team, admin, f"{setup}@b.co", "member")
        if setup == "unknown":
            token = "not-a-real-token"
        with pytest.raises(HTTPException) as e:
            svc.accept_invitation(db, token, uuid.uuid4(), email)
        bodies.append((e.value.status_code, e.value.detail))
    assert len(set(map(str, bodies))) == 1


# --- F2 regression: RLS now backs the teams tables (AI-Aug M07 review) --------

def test_another_tenant_cannot_see_this_teams_rows(db, tenant):
    """Migration 0012 gave teams/team_memberships/invitations row-level security.

    Before it, isolation on these tables was `require_role` alone; a query that
    skipped it — or an injection that reached them — crossed tenants. This proves
    the database itself now refuses, the same two-wall guarantee the links plane
    has. Written as the attack: create a team, a membership and an invitation as
    tenant A, then rebind to tenant B and confirm all three are invisible.
    """
    team = _team(db, tenant)  # binds tenant A
    admin = uuid.uuid4()
    _member(db, team, admin, "admin")
    svc.create_invitation(db, team, admin, "invited@a.co", "member")

    # Now act as a different tenant. RLS keys on app.tenant_id, so rebinding is
    # exactly what a second tenant's request does.
    other = uuid.uuid4()
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(other)})

    assert db.execute(text("SELECT count(*) FROM teams WHERE id = :i"),
                      {"i": str(team)}).scalar() == 0, "another tenant saw the team"
    assert db.execute(text("SELECT count(*) FROM team_memberships WHERE team_id = :t"),
                      {"t": str(team)}).scalar() == 0, "another tenant saw the membership"
    assert db.execute(text("SELECT count(*) FROM invitations WHERE team_id = :t"),
                      {"t": str(team)}).scalar() == 0, "another tenant saw the invitation"

    # And require_role, the application check, also default-denies across tenants.
    with pytest.raises(HTTPException) as e:
        svc.require_role(db, team, admin, "member")
    assert e.value.status_code == 404
