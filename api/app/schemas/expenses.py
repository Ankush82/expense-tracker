"""Story 3.1 request/response shapes and the validation the story's own
VALIDATION section requires. Every rejection here surfaces as a 422 with
a field-level error map (never a bare 500) -- `field_error` below is the
same shape FastAPI's own pydantic-validation 422 body uses, so a DB-level
check (category ownership, group membership) that the router itself must
perform reads identically to a pydantic-level one."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException, status
from pydantic import BaseModel, field_validator

# Not exhaustive ISO-4217 -- the currencies this product actually expects
# to see (an India-first expense tracker with occasional foreign-card
# spend). Extend this list rather than removing the check if a real user
# needs a currency it doesn't cover yet.
SUPPORTED_CURRENCIES = frozenset(
    {"INR", "USD", "EUR", "GBP", "AED", "SGD", "AUD", "CAD", "JPY", "CHF", "CNY", "HKD"}
)

_MAX_AMOUNT_MINOR = 100_000_000_000
_MIN_OCCURRED_AT_DATE = date(2000, 1, 1)


def field_error(field: str, msg: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=[{"loc": ["body", field], "msg": msg, "type": "value_error"}],
    )


class ExpenseCreate(BaseModel):
    amount_minor: int
    currency: str
    merchant: str
    occurred_at: datetime
    category_id: str | None = None
    notes: str | None = None
    group_id: str | None = None

    @field_validator("amount_minor")
    @classmethod
    def _validate_amount_minor(cls, v: int) -> int:
        if v <= 0 or v > _MAX_AMOUNT_MINOR:
            raise ValueError(f"amount_minor must be > 0 and <= {_MAX_AMOUNT_MINOR}")
        return v

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency: {v}")
        return v

    @field_validator("merchant")
    @classmethod
    def _validate_merchant(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 140):
            raise ValueError("merchant must be 1-140 chars after trim")
        return v

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 1000:
            raise ValueError("notes must be at most 1000 chars")
        return v

    @field_validator("occurred_at")
    @classmethod
    def _validate_occurred_at(cls, v: datetime) -> datetime:
        now = datetime.now(UTC)
        v_aware = v if v.tzinfo is not None else v.replace(tzinfo=UTC)
        if v_aware > now + timedelta(hours=24):
            raise ValueError("occurred_at must not be more than 24 hours in the future")
        if v_aware.date() < _MIN_OCCURRED_AT_DATE:
            raise ValueError("occurred_at must not be before 2000-01-01")
        return v

    model_config = {"extra": "forbid"}


class ExpenseUpdate(BaseModel):
    """PATCH body -- every field optional, unset fields are left alone.
    `model_fields_set` (checked in the router) is what distinguishes
    "field omitted" from "field explicitly set to null", which matters
    for nullable columns like category_id and notes."""

    amount_minor: int | None = None
    currency: str | None = None
    merchant: str | None = None
    occurred_at: datetime | None = None
    category_id: str | None = None
    notes: str | None = None
    group_id: str | None = None

    @field_validator("amount_minor")
    @classmethod
    def _validate_amount_minor(cls, v: int | None) -> int | None:
        if v is not None and (v <= 0 or v > _MAX_AMOUNT_MINOR):
            raise ValueError(f"amount_minor must be > 0 and <= {_MAX_AMOUNT_MINOR}")
        return v

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency: {v}")
        return v

    @field_validator("merchant")
    @classmethod
    def _validate_merchant(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not (1 <= len(v) <= 140):
            raise ValueError("merchant must be 1-140 chars after trim")
        return v

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 1000:
            raise ValueError("notes must be at most 1000 chars")
        return v

    @field_validator("occurred_at")
    @classmethod
    def _validate_occurred_at(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        now = datetime.now(UTC)
        v_aware = v if v.tzinfo is not None else v.replace(tzinfo=UTC)
        if v_aware > now + timedelta(hours=24):
            raise ValueError("occurred_at must not be more than 24 hours in the future")
        if v_aware.date() < _MIN_OCCURRED_AT_DATE:
            raise ValueError("occurred_at must not be before 2000-01-01")
        return v

    model_config = {"extra": "forbid"}


class ExpenseResponse(BaseModel):
    id: str
    user_id: str
    group_id: str | None
    amount_minor: int
    currency: str
    merchant: str
    category_id: str | None
    occurred_at: datetime
    notes: str | None
    source: str
    status: str
    created_at: datetime
    updated_at: datetime


class ExpenseListResponse(BaseModel):
    items: list[ExpenseResponse]
    next_cursor: str | None
