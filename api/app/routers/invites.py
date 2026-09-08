"""Story 2.2: create, list and revoke group invites (link and email).

Real, explicit scope gap: this story depends on 12.1 (notification
service abstraction), which does not exist yet -- an email invite's
underlying `group_invites` row is created for real here (email, role,
expiry, hashed token, all real), but no email is actually sent. Delivery
is deferred to whichever code implements 12.1's own service and calls
into this router's own resend path. `send_count`/`last_sent_at` are
still tracked correctly from creation, so the 3-per-day rate limit is
real and enforceable the moment delivery exists.

Accepting an invite (GET /join/{token} and the actual join transaction)
is Story 2.3's own scope, not this one's -- ENDPOINTS here is
create/list/revoke only. Revoking sets revoked_at for real, which is
exactly what a future accept endpoint would check to return "This
invite is no longer valid" (the story's own AC) -- nothing further to
build on this side for that AC.

Tokens: a real secrets.token_urlsafe(32) is generated per invite and
returned to the caller exactly once, in this endpoint's own response
body -- never logged (see this module's use of `token` vs `token_hash`
throughout: only token_hash ever reaches a log line, an exception
message, or a query filter on anything but the exact create call)."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import field_error
from app.core.security import get_current_user, require_group_role
from app.models.group_invite import GroupInvite
from app.models.group_member import GroupMember, GroupRole
from app.models.user import User
from app.schemas.invites import InviteCreateRequest, InviteResponse

router = APIRouter(prefix="/groups/{group_id}/invites", tags=["invites"])

_EXPIRES_IN = timedelta(days=7)
_LINK_MAX_USES = 25
_MAX_SENDS_PER_DAY = 3


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _to_response(invite: GroupInvite, *, link: str | None = None) -> InviteResponse:
    return InviteResponse(
        id=str(invite.id),
        group_id=str(invite.group_id),
        email=invite.email,
        role=GroupRole(invite.role).value,
        expires_at=invite.expires_at,
        max_uses=invite.max_uses,
        use_count=invite.use_count,
        revoked_at=invite.revoked_at,
        accepted_at=invite.accepted_at,
        created_at=invite.created_at,
        link=link,
    )


@router.post("", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
def create_invite(
    group_id: uuid.UUID,
    body: InviteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _membership: GroupMember = Depends(require_group_role(GroupRole.ADMIN)),
) -> InviteResponse:
    """admin+ only (require_group_role(ADMIN) covers both admin and
    owner, per its own rank comparison)."""
    if body.type == "email":
        if not body.email:
            raise field_error("email", "email is required for an email invite")

        already_member = db.execute(
            select(GroupMember).join(User, User.id == GroupMember.user_id).where(
                GroupMember.group_id == group_id,
                GroupMember.removed_at.is_(None),
                User.email == body.email,
            )
        ).scalar_one_or_none()
        if already_member is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{body.email} is already an active member of this group",
            )

        existing = db.execute(
            select(GroupInvite).where(GroupInvite.group_id == group_id, GroupInvite.email == body.email)
            .order_by(GroupInvite.created_at.desc())
        ).scalars().first()
        if existing is not None and existing.is_live:
            cutoff = datetime.now(UTC) - timedelta(days=1)
            if existing.last_sent_at is not None and existing.last_sent_at >= cutoff and existing.send_count >= _MAX_SENDS_PER_DAY:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"already sent {_MAX_SENDS_PER_DAY} invites to {body.email} for this group today",
                )
            # Resend: real re-issue of the token (a stale, possibly
            # already-leaked token from days ago should not silently
            # keep working forever just because "resend" was requested),
            # same row, no duplicate.
            token = secrets.token_urlsafe(32)
            existing.token_hash = _hash_token(token)
            existing.expires_at = datetime.now(UTC) + _EXPIRES_IN
            existing.send_count = existing.send_count + 1 if (existing.last_sent_at and existing.last_sent_at >= cutoff) else 1
            existing.last_sent_at = datetime.now(UTC)
            db.commit()
            db.refresh(existing)
            return _to_response(existing, link=f"/join/{token}")

    token = secrets.token_urlsafe(32)
    invite = GroupInvite(
        group_id=group_id,
        invited_by=current_user.id,
        email=body.email if body.type == "email" else None,
        token_hash=_hash_token(token),
        role=GroupRole(body.role),
        expires_at=datetime.now(UTC) + _EXPIRES_IN,
        max_uses=None if body.type == "email" else _LINK_MAX_USES,
        send_count=1,
        last_sent_at=datetime.now(UTC) if body.type == "email" else None,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return _to_response(invite, link=f"/join/{token}")


@router.get("", response_model=list[InviteResponse])
def list_invites(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _membership: GroupMember = Depends(require_group_role(GroupRole.ADMIN)),
) -> list[InviteResponse]:
    """Pending invites only -- live per GroupInvite.is_live (not
    accepted, not revoked, not expired). A past, resolved invite is real
    history but not what "pending invites" (the story's own words)
    means here."""
    invites = db.execute(
        select(GroupInvite).where(GroupInvite.group_id == group_id).order_by(GroupInvite.created_at.desc())
    ).scalars().all()
    return [_to_response(i) for i in invites if i.is_live]


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def revoke_invite(
    group_id: uuid.UUID,
    invite_id: uuid.UUID,
    db: Session = Depends(get_db),
    _membership: GroupMember = Depends(require_group_role(GroupRole.ADMIN)),
) -> None:
    invite = db.get(GroupInvite, invite_id)
    if invite is None or invite.group_id != group_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
        db.commit()
