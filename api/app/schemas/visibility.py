"""Story 11.1 request/response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class VisibilityUpdateRequest(BaseModel):
    level: Literal["full", "aggregate", "hidden"]
    hide_categories: list[str] = []
    # Deliberately accepted (not just ignored) so the story's own AC --
    # "An owner attempting to change another member's setting gets 403"
    # -- is a real, checkable request shape: if a caller names a
    # DIFFERENT user, the router 403s explicitly rather than silently
    # applying the change to the caller's own setting regardless of what
    # was asked for. Omit it (the normal case) to mean "my own setting".
    user_id: str | None = None

    model_config = {"extra": "forbid"}


class VisibilityResponse(BaseModel):
    group_id: str
    user_id: str
    level: str
    hide_categories: list[str]
    updated_at: datetime | None  # None when this is the real, unset default -- no row exists yet


class MemberVisibilitySummary(BaseModel):
    user_id: str
    level: str


class VisibilitySummaryResponse(BaseModel):
    members: list[MemberVisibilitySummary]
