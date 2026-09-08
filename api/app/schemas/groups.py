"""Story 2.1 request/response shapes."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator

from app.schemas.expenses import SUPPORTED_CURRENCIES

_MIN_NAME_LEN = 2
_MAX_NAME_LEN = 60


def _validate_name(v: str) -> str:
    v = v.strip()
    if not (_MIN_NAME_LEN <= len(v) <= _MAX_NAME_LEN):
        raise ValueError(f"name must be {_MIN_NAME_LEN}-{_MAX_NAME_LEN} chars after trim")
    return v


def _validate_currency(v: str) -> str:
    v = v.upper()
    if v not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported currency: {v}")
    return v


class GroupCreate(BaseModel):
    name: str
    default_currency: str = "INR"

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("default_currency")
    @classmethod
    def _check_currency(cls, v: str) -> str:
        return _validate_currency(v)

    model_config = {"extra": "forbid"}


class GroupUpdate(BaseModel):
    name: str | None = None
    default_currency: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return _validate_name(v) if v is not None else v

    @field_validator("default_currency")
    @classmethod
    def _check_currency(cls, v: str | None) -> str | None:
        return _validate_currency(v) if v is not None else v

    model_config = {"extra": "forbid"}


class GroupMemberResponse(BaseModel):
    user_id: str
    role: str
    joined_at: datetime


class GroupResponse(BaseModel):
    id: str
    name: str
    default_currency: str
    created_at: datetime
    member_count: int
    caller_role: str


class GroupDetailResponse(BaseModel):
    id: str
    name: str
    default_currency: str
    created_at: datetime
    members: list[GroupMemberResponse]
