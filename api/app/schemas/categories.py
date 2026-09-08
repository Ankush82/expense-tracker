"""Story 3.4 request/response shapes."""
from __future__ import annotations

from pydantic import BaseModel, field_validator

_MAX_NAME_LEN = 100


class CategoryCreate(BaseModel):
    name: str
    icon: str | None = None
    color: str | None = None
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= _MAX_NAME_LEN):
            raise ValueError(f"name must be 1-{_MAX_NAME_LEN} chars after trim")
        return v

    model_config = {"extra": "forbid"}


class CategoryUpdate(BaseModel):
    name: str | None = None
    icon: str | None = None
    color: str | None = None
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not (1 <= len(v) <= _MAX_NAME_LEN):
            raise ValueError(f"name must be 1-{_MAX_NAME_LEN} chars after trim")
        return v

    model_config = {"extra": "forbid"}


class CategoryResponse(BaseModel):
    id: str
    name: str
    icon: str | None
    color: str | None
    is_system: bool
    parent_id: str | None
    owner_user_id: str | None
    hidden: bool
