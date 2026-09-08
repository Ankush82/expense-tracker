"""Story 9.1: the single recorder every domain event goes through.

"Producers must not write feed rows directly from endpoint handlers;
they emit a domain event that a single recorder consumes, so the feed
cannot drift from reality" (the story's own words, verbatim). `record()`
below is that one recorder -- every router that causes one of the v1
event types calls this, never `db.add(GroupActivity(...))` directly.

record() does NOT commit -- it `db.add()`s onto the SAME session/
transaction the caller's own domain write is already in, so the feed
entry and the real change it describes (an expense created, a budget
updated, ...) commit or roll back together atomically. A caller that
committed its own change first and only then called record() (in a
separate transaction) would risk a real change with no matching feed
entry if the process died in between -- record() being part of the same
transaction is what rules that out.

Real, known scope gap (STORY 9.1's own AC: "Every listed event type is
emitted by its owning service and appears in the feed"): several v1
event types belong to stories that don't exist yet --
member_joined/member_left/member_removed/role_changed (Story 2.3/2.4,
group join/member-management endpoints not built), expense_split_created
/settlement_recorded (Epic 15, not built), comment_added (Story 9.3, not
built). _KNOWN_EVENT_TYPES below lists all 13 real v1 types so record()
validates against the full, real vocabulary even before every emitter
exists -- but only expense_added/expense_edited/expense_deleted
(app.routers.expenses) and budget_created/budget_updated
(app.routers.budgets) are actually wired to call record() today. The
rest are real, explicit TODOs for whichever story adds their owning
endpoint."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.group_activity import GroupActivity

_KNOWN_EVENT_TYPES = frozenset(
    {
        "member_joined",
        "member_left",
        "member_removed",
        "role_changed",
        "expense_added",
        "expense_edited",
        "expense_deleted",
        "budget_created",
        "budget_updated",
        "budget_exceeded",
        "expense_split_created",
        "settlement_recorded",
        "comment_added",
    }
)


def record(
    db: Session,
    *,
    group_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    event_type: str,
    entity_type: str,
    entity_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if event_type not in _KNOWN_EVENT_TYPES:
        raise ValueError(f"unknown activity event_type: {event_type!r}")
    db.add(
        GroupActivity(
            group_id=group_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            event_metadata=metadata,
        )
    )
