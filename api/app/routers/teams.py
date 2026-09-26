"""Team collaboration routes. Thin: HTTP in, service out, no logic here."""

import uuid

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app import ratelimit
from app.auth import Principal, get_tenant_session, require_principal
from app.services import teams_service

router = APIRouter(prefix="/teams", tags=["teams"])
invites = APIRouter(prefix="/invitations", tags=["teams"])


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="member")


class InviteResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    email: str
    role: str
    expires_at: str
    token: str | None = None


class AcceptResponse(BaseModel):
    team_id: uuid.UUID
    role: str


@router.post("/{team_id}/invitations", status_code=status.HTTP_201_CREATED)
def send_invitation(
    team_id: uuid.UUID,
    body: InviteRequest,
    request: Request,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_tenant_session),
):
    ratelimit.check("create", str(principal.tenant_id))
    row, token = teams_service.create_invitation(
        db, team_id, principal.user_id, body.email, body.role
    )
    return InviteResponse(
        id=row["id"],
        team_id=row["team_id"],
        email=row["email"],
        role=row["role"],
        expires_at=row["expires_at"].isoformat().replace("+00:00", "Z"),
        token=token or None,
    )


@invites.post("/{token}/accept")
def accept_invitation(
    token: str,
    request: Request,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_tenant_session),
):
    ratelimit.check("auth_fail", str(principal.user_id))
    result = teams_service.accept_invitation(db, token, principal.user_id, principal.email)
    return AcceptResponse(**result)


@router.delete("/{team_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(
    team_id: uuid.UUID,
    invitation_id: uuid.UUID,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_tenant_session),
):
    teams_service.revoke_invitation(db, team_id, invitation_id, principal.user_id)
