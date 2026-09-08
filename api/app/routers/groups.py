"""Story 2.1: group CRUD -- create, list, view, rename, delete.

Builds on Story 0.4's minimal GET/DELETE (kept, extended) and its
require_group_role/get_current_user authorization layer -- nothing here
re-implements membership/role checks, all of it goes through the same
dependency Story 0.4 built.

Invite links, join-by-invite, and member removal are Epic 2's later
stories (2.2/2.3/2.4) -- POST /groups is the only member-adding action
this story's own endpoints expose, so the "a group may hold at most 50
members" business rule has no real second call site to enforce here yet
(a group created by this endpoint always starts at exactly 1 member).
Left as an explicit note for whichever of those stories adds the next
member-adding endpoint, which must check the same cap."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import field_error
from app.core.security import get_current_user, require_group_role
from app.models.expense import Expense
from app.models.group import Group
from app.models.group_member import GroupMember, GroupRole
from app.models.user import User
from app.schemas.groups import (
    GroupCreate,
    GroupDetailResponse,
    GroupMemberResponse,
    GroupResponse,
    GroupUpdate,
)

router = APIRouter(prefix="/groups", tags=["groups"])

# Story's own business rule: "A user may belong to at most 20 groups...
# in v1." Checked at the one real member-adding call site this story
# owns (POST /groups, the creator's own membership) -- see this module's
# own docstring for why the 50-members-per-group side of the same rule
# has no real enforcement point here yet.
_MAX_GROUPS_PER_USER = 20


def _active_member_count(db: Session, group_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count())
        .select_from(GroupMember)
        .where(GroupMember.group_id == group_id, GroupMember.removed_at.is_(None))
    ).scalar_one()


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    body: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GroupResponse:
    """Creates the group and adds the caller as owner in the same
    transaction -- the story's own AC: "Creator is automatically owner
    and appears in the member list." A single commit, so a failure
    partway through (e.g. the uniqueness constraint) leaves neither the
    group nor a dangling membership behind."""
    active_group_count = db.execute(
        select(func.count())
        .select_from(GroupMember)
        .where(GroupMember.user_id == current_user.id, GroupMember.removed_at.is_(None))
    ).scalar_one()
    if active_group_count >= _MAX_GROUPS_PER_USER:
        raise field_error("name", f"you may belong to at most {_MAX_GROUPS_PER_USER} groups")

    group = Group(name=body.name, created_by=current_user.id, default_currency=body.default_currency)
    db.add(group)

    try:
        # flush (not just commit) is inside this try -- it's what actually
        # issues the INSERT and can raise the real uniqueness violation,
        # since group.id (needed for the membership row below) is
        # server-generated (gen_random_uuid()), not known until then. A
        # try that only wrapped commit() below would miss it here.
        db.flush()
        db.add(GroupMember(group_id=group.id, user_id=current_user.id, role=GroupRole.OWNER))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise field_error("name", "you already have a group with this name")
    db.refresh(group)

    return GroupResponse(
        id=str(group.id),
        name=group.name,
        default_currency=group.default_currency,
        created_at=group.created_at,
        member_count=1,
        caller_role=GroupRole.OWNER.value,
    )


@router.get("", response_model=list[GroupResponse])
def list_groups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[GroupResponse]:
    """Groups the caller is an ACTIVE member of -- a group the caller was
    removed from, or a soft-deleted group, is invisible here, same
    "removed_at IS NULL is the one real source of truth" rule Story 0.4
    already established for membership."""
    memberships = db.execute(
        select(GroupMember).where(GroupMember.user_id == current_user.id, GroupMember.removed_at.is_(None))
    ).scalars().all()

    result: list[GroupResponse] = []
    for membership in memberships:
        group = db.get(Group, membership.group_id)
        if group is None or group.deleted_at is not None:
            continue
        result.append(
            GroupResponse(
                id=str(group.id),
                name=group.name,
                default_currency=group.default_currency,
                created_at=group.created_at,
                member_count=_active_member_count(db, group.id),
                caller_role=GroupRole(membership.role).value,
            )
        )
    return result


@router.get("/{group_id}", response_model=GroupDetailResponse)
def get_group(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _membership: GroupMember = Depends(require_group_role(GroupRole.MEMBER)),
) -> GroupDetailResponse:
    group = db.get(Group, group_id)
    if group is None or group.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")

    active_members = db.execute(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.removed_at.is_(None))
    ).scalars().all()

    return GroupDetailResponse(
        id=str(group.id),
        name=group.name,
        default_currency=group.default_currency,
        created_at=group.created_at,
        members=[
            GroupMemberResponse(user_id=str(m.user_id), role=GroupRole(m.role).value, joined_at=m.joined_at)
            for m in active_members
        ],
    )


@router.patch("/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: uuid.UUID,
    body: GroupUpdate,
    db: Session = Depends(get_db),
    membership: GroupMember = Depends(require_group_role(GroupRole.ADMIN)),
) -> GroupResponse:
    """Owner/admin only (the story's own AC: "A member (non-admin)
    receives 403 on PATCH"). Changing default_currency does NOT
    retroactively touch any existing expense -- the story's own explicit
    rule; nothing here writes to the expenses table at all."""
    group = db.get(Group, group_id)
    if group is None or group.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")

    update_fields = body.model_fields_set
    if "name" in update_fields:
        if body.name is None:
            # name is NOT NULL at the DB level -- reject explicitly here
            # rather than let an IntegrityError from the commit below get
            # mistaken for the name-uniqueness violation it also catches.
            raise field_error("name", "name cannot be null")
        group.name = body.name
    if "default_currency" in update_fields:
        if body.default_currency is None:
            raise field_error("default_currency", "default_currency cannot be null")
        group.default_currency = body.default_currency
    group.updated_at = datetime.now(UTC)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise field_error("name", "you already have a group with this name")
    db.refresh(group)

    return GroupResponse(
        id=str(group.id),
        name=group.name,
        default_currency=group.default_currency,
        created_at=group.created_at,
        member_count=_active_member_count(db, group.id),
        caller_role=GroupRole(membership.role).value,
    )


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_group(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _membership: GroupMember = Depends(require_group_role(GroupRole.OWNER)),
) -> None:
    """Soft-deletes the group -- requires OWNER (the story's AC: "a
    member cannot delete a group" -- an admin can't either, only the
    owner). Real, explicit UNLINK, not a cascade delete: the story's own
    rule is "expenses keep user_id, get group_id = null, and remain in
    each member's personal history" -- both the group's own soft delete
    and every one of its expenses' group_id update happen in the same
    transaction, so a crash partway through never leaves expenses
    pointing at a group that's already gone."""
    group = db.get(Group, group_id)
    if group is None or group.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")

    db.execute(
        update(Expense)
        .where(Expense.group_id == group_id)
        .values(group_id=None, updated_at=datetime.now(UTC))
    )
    group.deleted_at = datetime.now(UTC)
    db.commit()
