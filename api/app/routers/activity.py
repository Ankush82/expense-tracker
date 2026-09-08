"""Story 9.1: GET /groups/{id}/activity -- the read side of the feed.

Rule enforced here (the story's own words): "Feed entries respect Epic
11 visibility: if a member hides expense detail, their expense_added
event renders as 'Priya added an expense' with no merchant or amount."
Applied per-event, keyed on the ACTING user's own member_visibility
level in this group (level != full for an expense_* event redacts its
metadata) -- never the viewer's own level, and never applied to a
viewer looking at their OWN events (you always see your own real data;
the level only restricts what OTHER members see of you).

Collapsing consecutive similar events by the same actor within 10
minutes ("Ankush added 4 expenses") is explicitly a CLIENT-side rule
per the story's own text -- this endpoint returns real, individual
rows, uncollapsed."""
from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import field_error
from app.core.security import require_group_role
from app.models.group_activity import GroupActivity
from app.models.group_member import GroupMember, GroupRole
from app.models.member_visibility import MemberVisibility, VisibilityLevel
from app.schemas.activity import ActivityEventResponse, ActivityFeedResponse

router = APIRouter(prefix="/groups/{group_id}/activity", tags=["activity"])

_DEFAULT_LIMIT = 20
_REDACTED_EVENT_TYPES = frozenset({"expense_added", "expense_edited", "expense_deleted"})


def _encode_cursor(event_id: int) -> str:
    return base64.urlsafe_b64encode(str(event_id).encode()).decode()


def _decode_cursor(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise field_error("cursor", "invalid cursor") from exc


@router.get("", response_model=ActivityFeedResponse)
def get_activity_feed(
    group_id: uuid.UUID,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=100),
    types: list[str] | None = Query(default=None),
    membership: GroupMember = Depends(require_group_role(GroupRole.MEMBER)),
    db: Session = Depends(get_db),
) -> ActivityFeedResponse:
    query = select(GroupActivity).where(GroupActivity.group_id == group_id)
    if types:
        query = query.where(GroupActivity.event_type.in_(types))
    if cursor is not None:
        query = query.where(GroupActivity.id < _decode_cursor(cursor))

    query = query.order_by(GroupActivity.id.desc()).limit(limit + 1)
    rows = list(db.execute(query).scalars().all())

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].id)

    # Redaction: one query for every DISTINCT actor's own visibility
    # level in this group, not one query per row -- the same real
    # N+1-avoidance discipline this codebase already applies elsewhere
    # (Story 3.3's bulk endpoint, this story's own sibling PRs).
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id is not None}
    levels_by_actor: dict[uuid.UUID, str] = {}
    if actor_ids:
        for v in db.execute(
            select(MemberVisibility).where(
                MemberVisibility.group_id == group_id, MemberVisibility.user_id.in_(actor_ids)
            )
        ).scalars():
            levels_by_actor[v.user_id] = VisibilityLevel(v.level).value

    items = []
    for row in rows:
        event_metadata = row.event_metadata
        is_own_event = row.actor_user_id == membership.user_id
        actor_level = levels_by_actor.get(row.actor_user_id, VisibilityLevel.AGGREGATE.value) if row.actor_user_id else None
        if not is_own_event and row.event_type in _REDACTED_EVENT_TYPES and actor_level != VisibilityLevel.FULL.value:
            event_metadata = {"redacted": True}
        items.append(
            ActivityEventResponse(
                id=str(row.id),
                group_id=str(row.group_id),
                actor_user_id=str(row.actor_user_id) if row.actor_user_id else None,
                event_type=row.event_type,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                metadata=event_metadata,
                created_at=row.created_at,
            )
        )

    return ActivityFeedResponse(items=items, next_cursor=next_cursor)
