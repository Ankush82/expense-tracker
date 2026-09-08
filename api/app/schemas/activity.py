"""Story 9.1 request/response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ActivityEventResponse(BaseModel):
    id: str
    group_id: str
    actor_user_id: str | None
    event_type: str
    entity_type: str
    entity_id: str
    metadata: dict[str, Any] | None
    created_at: datetime


class ActivityFeedResponse(BaseModel):
    items: list[ActivityEventResponse]
    next_cursor: str | None
