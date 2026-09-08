"""Story 11.1: per-group, per-user visibility settings.

Real, deliberate rule (the story's own words, worth restating here since
it's easy to get backwards): "Group owners CANNOT override a member's
visibility setting." Every endpoint below only ever reads or writes the
CALLER's own row -- there is no path, at any role, to change someone
else's setting. GET .../summary exposes only each member's LEVEL (never
their hide_categories or actual data) precisely so the UI can explain
why data is missing without leaking anything a hidden/aggregate member
didn't choose to share.

No row existing for a (group_id, user_id) is a real, meaningful state --
"aggregate, the default, nothing customized yet" -- not an error and not
something a group-join flow needs to remember to insert (Story 2.3, the
actual join endpoint, doesn't exist yet; this story's own AC "default on
join is aggregate" is satisfied here by GET returning that default
whether or not a row exists, so it's correct from the moment 2.3 does
land, with no coordination required between the two stories)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_group_role
from app.models.group_member import GroupMember, GroupRole
from app.models.member_visibility import MemberVisibility, VisibilityLevel
from app.schemas.visibility import (
    MemberVisibilitySummary,
    VisibilityResponse,
    VisibilitySummaryResponse,
    VisibilityUpdateRequest,
)

router = APIRouter(prefix="/groups/{group_id}/visibility", tags=["visibility"])

_DEFAULT_LEVEL = VisibilityLevel.AGGREGATE


def _row_or_none(db: Session, group_id: uuid.UUID, user_id: uuid.UUID) -> MemberVisibility | None:
    return db.execute(
        select(MemberVisibility).where(
            MemberVisibility.group_id == group_id, MemberVisibility.user_id == user_id
        )
    ).scalar_one_or_none()


def _to_response(group_id: uuid.UUID, user_id: uuid.UUID, row: MemberVisibility | None) -> VisibilityResponse:
    if row is None:
        return VisibilityResponse(
            group_id=str(group_id), user_id=str(user_id), level=_DEFAULT_LEVEL.value,
            hide_categories=[], updated_at=None,
        )
    return VisibilityResponse(
        group_id=str(row.group_id), user_id=str(row.user_id), level=VisibilityLevel(row.level).value,
        hide_categories=[str(c) for c in row.hide_categories], updated_at=row.updated_at,
    )


@router.get("", response_model=VisibilityResponse)
def get_own_visibility(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: GroupMember = Depends(require_group_role(GroupRole.MEMBER)),
) -> VisibilityResponse:
    row = _row_or_none(db, group_id, membership.user_id)
    return _to_response(group_id, membership.user_id, row)


@router.put("", response_model=VisibilityResponse)
def update_own_visibility(
    group_id: uuid.UUID,
    body: VisibilityUpdateRequest,
    db: Session = Depends(get_db),
    membership: GroupMember = Depends(require_group_role(GroupRole.MEMBER)),
) -> VisibilityResponse:
    """Own setting only, unconditionally -- see this module's own
    docstring. body.user_id, when given, must name the caller; anything
    else is the story's own AC made real: "An owner attempting to
    change another member's setting gets 403" (true for every role, not
    just an owner -- the story singles out an owner because that's the
    most privileged role in the group, making the point that even the
    MOST privileged member still can't do this)."""
    if body.user_id is not None:
        try:
            target_user_id = uuid.UUID(body.user_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="user_id is not a valid UUID")
        if target_user_id != membership.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="you may only change your own visibility setting",
            )

    try:
        hide_category_ids = [uuid.UUID(c) for c in body.hide_categories]
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="hide_categories contains an invalid UUID")

    row = _row_or_none(db, group_id, membership.user_id)
    if row is None:
        row = MemberVisibility(
            group_id=group_id, user_id=membership.user_id,
            level=VisibilityLevel(body.level), hide_categories=hide_category_ids,
        )
        db.add(row)
    else:
        row.level = VisibilityLevel(body.level)
        row.hide_categories = hide_category_ids
        from datetime import UTC, datetime

        row.updated_at = datetime.now(UTC)

    # Real, synchronous write -- no cache, no queue, nothing buffered.
    # The story's own AC ("switching to hidden removes the member...
    # within one request cycle") is satisfied by this commit alone; the
    # actual CONSUMPTION of this setting by group dashboards/breakdowns
    # is Epic 7's own scope (not built yet) and must read this table
    # fresh on every real request, not cache it.
    db.commit()
    db.refresh(row)
    return _to_response(group_id, membership.user_id, row)


@router.get("/summary", response_model=VisibilitySummaryResponse)
def get_visibility_summary(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _membership: GroupMember = Depends(require_group_role(GroupRole.MEMBER)),
) -> VisibilitySummaryResponse:
    """Every active member's LEVEL only -- never hide_categories, never
    any of their actual expense data. "so the UI can explain why data
    is missing" (the story's own words) needs exactly this much, no
    more."""
    active_members = db.execute(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.removed_at.is_(None))
    ).scalars().all()

    rows_by_user = {
        row.user_id: row
        for row in db.execute(select(MemberVisibility).where(MemberVisibility.group_id == group_id)).scalars()
    }

    return VisibilitySummaryResponse(
        members=[
            MemberVisibilitySummary(
                user_id=str(m.user_id),
                level=VisibilityLevel(rows_by_user[m.user_id].level).value
                if m.user_id in rows_by_user
                else _DEFAULT_LEVEL.value,
            )
            for m in active_members
        ]
    )
