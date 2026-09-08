"""Minimal group endpoints, scoped to exactly what Story 0.4 needs to
prove its own authorization layer end-to-end (the story's AC requires
"integration tests prove a member cannot delete a group" -- that needs
a real route to call, not just a unit test of the dependency function
in isolation). Epic 2 (Group Management) owns the real, full group
CRUD surface -- invite links, renaming, member management, etc. -- and
may replace or extend these two routes; nothing here should be read as
Epic 2's implementation."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_group_role
from app.models.group import Group
from app.models.group_member import GroupMember, GroupRole

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("/{group_id}")
def get_group(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _membership: GroupMember = Depends(require_group_role(GroupRole.MEMBER)),
) -> dict:
    group = db.get(Group, group_id)
    if group is None or group.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    return {
        "id": str(group.id),
        "name": group.name,
        "default_currency": group.default_currency,
        "created_at": group.created_at.isoformat(),
    }


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_group(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _membership: GroupMember = Depends(require_group_role(GroupRole.OWNER)),
) -> None:
    """Soft-deletes the group -- requires OWNER (the story's AC: "a
    member cannot delete a group" -- an admin can't either, only the
    owner). Real soft delete (deleted_at set), matching Story 0.2's
    schema; nothing here cascades to expenses/members, since that
    real cross-table behaviour is Epic 2's own scope to design."""
    group = db.get(Group, group_id)
    if group is None or group.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    group.deleted_at = datetime.now(UTC)
    db.commit()
