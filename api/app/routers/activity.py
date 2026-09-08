"""Story 9.1 (feed) + Story 11.2 (visibility enforcement): GET
/groups/{id}/activity -- the read side of the feed.

Two distinct real behaviors, matching Story 11.1's own definitions of
"aggregate" vs "hidden" exactly (this router's redaction used to treat
them identically -- Story 11.2 splits them for real):
  - level=hidden: "the user contributes nothing to group views" (Story
    11.1's own words) -- their expense_* events are excluded from the
    feed ENTIRELY, at the DB-query layer, before pagination is applied
    (never fetched, then hidden -- Story 11.2's own explicit rule: "Never
    fetch-then-hide in the client").
  - level=aggregate (or no row yet -- Story 11.1's own default):
    "totals and category shares, but not individual merchants or
    amounts" -- the EVENT still appears (something real happened, that
    much is a legitimate aggregate-level signal), but its metadata is
    redacted to {"redacted": true}, matching the story's own example:
    "Priya added an expense" with no merchant or amount.
  - level=full: real, complete metadata.

Applied per-event, keyed on the ACTING user's own level in this group --
never the viewer's own level, and never applied to a viewer looking at
their OWN events (Story 11.1: "The viewer always sees their own data in
full").

Collapsing consecutive similar events by the same actor within 10
minutes ("Ankush added 4 expenses") is explicitly a CLIENT-side rule
per the story's own text -- this endpoint returns real, individual
rows, uncollapsed."""
from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import not_, or_, select
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

    # Story 11.2: exclude a hidden-level actor's events entirely, in the
    # query itself -- before pagination, not after. "id" (not just
    # user_id) in the correlation keeps this a real correlated subquery
    # per row rather than an accidental cross join.
    actor_is_hidden = (
        select(MemberVisibility.id)
        .where(
            MemberVisibility.group_id == GroupActivity.group_id,
            MemberVisibility.user_id == GroupActivity.actor_user_id,
            MemberVisibility.level == VisibilityLevel.HIDDEN.value,
        )
        .exists()
    )
    query = query.where(
        or_(
            GroupActivity.actor_user_id == membership.user_id,  # always see your own
            GroupActivity.actor_user_id.is_(None),  # a system/no-actor event, if one ever exists
            not_(actor_is_hidden),
        )
    )

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
