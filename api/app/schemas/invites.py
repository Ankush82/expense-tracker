"""Story 2.2 request/response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator


class InviteCreateRequest(BaseModel):
    type: Literal["link", "email"]
    email: str | None = None
    role: Literal["admin", "member"] = "member"

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip().lower()
            if "@" not in v or len(v) < 3:
                raise ValueError("not a valid email address")
        return v

    model_config = {"extra": "forbid"}


class InviteResponse(BaseModel):
    id: str
    group_id: str
    email: str | None
    role: str
    expires_at: datetime
    max_uses: int | None
    use_count: int
    revoked_at: datetime | None
    accepted_at: datetime | None
    created_at: datetime
    # Present ONLY in the create-response, and ONLY there -- the one
    # moment the raw token is ever returned. None on every other
    # response (list, etc.) since only the hash is ever stored or
    # re-derivable.
    link: str | None = None
