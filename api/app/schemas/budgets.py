"""Story 6.1 request/response shapes."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, field_validator

from app.schemas.expenses import SUPPORTED_CURRENCIES

_MAX_AMOUNT_MINOR = 100_000_000_000


class BudgetCreate(BaseModel):
    scope: Literal["user", "group"]
    group_id: str | None = None
    category_id: str | None = None
    period: Literal["daily", "weekly", "monthly", "custom"]
    period_start: date | None = None
    period_end: date | None = None
    amount_minor: int
    currency: str = "INR"
    rollover: bool = False
    alert_thresholds: list[int] = [80, 100]

    @field_validator("amount_minor")
    @classmethod
    def _check_amount(cls, v: int) -> int:
        if v <= 0 or v > _MAX_AMOUNT_MINOR:
            raise ValueError(f"amount_minor must be > 0 and <= {_MAX_AMOUNT_MINOR}")
        return v

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency: {v}")
        return v

    @field_validator("alert_thresholds")
    @classmethod
    def _check_thresholds(cls, v: list[int]) -> list[int]:
        for t in v:
            if not (0 < t <= 200):
                raise ValueError("alert_thresholds entries must be between 1 and 200 (percent)")
        return v

    model_config = {"extra": "forbid"}


class BudgetUpdate(BaseModel):
    category_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    rollover: bool | None = None
    alert_thresholds: list[int] | None = None
    is_active: bool | None = None

    @field_validator("amount_minor")
    @classmethod
    def _check_amount(cls, v: int | None) -> int | None:
        if v is not None and (v <= 0 or v > _MAX_AMOUNT_MINOR):
            raise ValueError(f"amount_minor must be > 0 and <= {_MAX_AMOUNT_MINOR}")
        return v

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency: {v}")
        return v

    model_config = {"extra": "forbid"}


class BudgetResponse(BaseModel):
    id: str
    scope: str
    user_id: str | None
    group_id: str | None
    category_id: str | None
    period: str
    period_start: date
    period_end: date
    amount_minor: int
    currency: str
    rollover: bool
    alert_thresholds: list[int]
    is_active: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
