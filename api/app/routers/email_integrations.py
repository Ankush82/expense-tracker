"""Story 4.2 endpoints: per-user sender allow/block overrides, and the
admin endpoint for the global transaction_senders registry. The Gmail
sync itself (Story 4.3) and message parsing (Story 5.1) are separate
stories -- nothing here talks to Gmail."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user, require_global_admin
from app.models.transaction_sender import TransactionSender
from app.models.user import User
from app.models.user_sender_override import UserSenderOverride
from app.schemas.email_integrations import (
    AdminSenderCreateRequest,
    SenderOverrideRequest,
    SenderOverrideResponse,
    TransactionSenderResponse,
)

router = APIRouter(tags=["email-integrations"])


@router.post("/integrations/email/senders", response_model=SenderOverrideResponse)
def upsert_sender_override(
    body: SenderOverrideRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SenderOverrideResponse:
    """Adds (or updates) the caller's own allow/block override for one
    sender. A second POST for the same sender REPLACES the prior
    action rather than creating a duplicate row (the unique index on
    (user_id, sender_pattern) is what makes this a real upsert, not
    just an application-level convention)."""
    existing = db.execute(
        select(UserSenderOverride).where(
            UserSenderOverride.user_id == current_user.id,
            UserSenderOverride.sender_pattern == body.sender,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.action = body.action
    else:
        existing = UserSenderOverride(
            user_id=current_user.id, sender_pattern=body.sender, action=body.action
        )
        db.add(existing)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="override for this sender changed concurrently, retry"
        )

    return SenderOverrideResponse(sender_pattern=existing.sender_pattern, action=existing.action)


@router.post(
    "/admin/transaction-senders",
    response_model=TransactionSenderResponse,
    status_code=status.HTTP_201_CREATED,
)
def admin_add_sender(
    body: AdminSenderCreateRequest,
    _admin: User = Depends(require_global_admin),
    db: Session = Depends(get_db),
) -> TransactionSenderResponse:
    """Adds a sender to the GLOBAL registry without a deploy (the
    story's own explicit requirement) -- admin-only, see
    require_global_admin's own docstring for how "admin" is decided
    today."""
    sender = TransactionSender(
        sender_pattern=body.sender_pattern,
        institution_name=body.institution_name,
        country=body.country.upper(),
        parser_key=body.parser_key,
        is_active=body.is_active,
        notes=body.notes,
    )
    db.add(sender)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="a sender with this pattern already exists"
        )
    db.refresh(sender)

    return TransactionSenderResponse(
        id=str(sender.id),
        sender_pattern=sender.sender_pattern,
        institution_name=sender.institution_name,
        country=sender.country,
        parser_key=sender.parser_key,
        is_active=sender.is_active,
        notes=sender.notes,
    )
