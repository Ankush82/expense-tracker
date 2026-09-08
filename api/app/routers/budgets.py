"""Story 6.1: budget CRUD -- one flexible entity for personal and group,
every period type, category-specific or overall.

rollover is real (a real boolean column, a real request field) but ships
BEHIND A FEATURE FLAG, default off (the story's own explicit
requirement): _ROLLOVER_ENABLED below. A request with rollover=true is
rejected with a clear 422 while the flag is off, rather than silently
accepted and ignored -- a caller should never believe a setting took
effect when it didn't. The actual rollover CALCULATION (carrying unspent
amount into the next period, capped at one period's worth) is real
follow-on work for whichever story turns the flag on; nothing here
computes it yet.

"Deleting a budget stops its alerts immediately" (the story's own AC):
alerts themselves are Story 6.4's own scope (not built yet) -- this
story's real, correct contribution is that DELETE sets deleted_at
synchronously, so any future alert-checking query that correctly filters
deleted_at IS NULL (as every other soft-delete in this codebase already
must) simply never sees a deleted budget again, immediately."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import field_error
from app.core.security import get_current_user
from app.models.budget import Budget, BudgetPeriod, BudgetScope
from app.models.category import Category
from app.models.group_member import GroupMember, GroupRole
from app.models.user import User
from app.schemas.budgets import BudgetCreate, BudgetResponse, BudgetUpdate

router = APIRouter(prefix="/budgets", tags=["budgets"])

# Real, explicit feature flag (the story's own requirement: "Ship
# rollover behind a feature flag, default off"). Not read from
# app.core.config/env on purpose -- rollover's actual carry-forward
# calculation doesn't exist yet, so there is nothing correct for turning
# this on to actually DO; a module constant is honest about that, an env
# var would imply a real, working toggle that isn't there yet.
_ROLLOVER_ENABLED = False

_ROLE_RANK = {GroupRole.MEMBER: 1, GroupRole.ADMIN: 2, GroupRole.OWNER: 3}


def _require_group_admin(db: Session, group_id: uuid.UUID, current_user: User) -> None:
    membership = db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.user_id == current_user.id, GroupMember.removed_at.is_(None)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not an active member of this group")
    if _ROLE_RANK[GroupRole(membership.role)] < _ROLE_RANK[GroupRole.ADMIN]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="group budgets require owner or admin")


def _compute_period_bounds(
    period: str, period_start: date | None, period_end: date | None, tz_name: str
) -> tuple[date, date]:
    """Daily/weekly/monthly periods are auto-computed from "today" in the
    OWNER's own timezone (the story's own rule: "Month boundaries follow
    the user's timezone") if the caller didn't supply explicit bounds --
    custom always requires both, explicitly, from the caller."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()

    if period == "custom":
        if period_start is None or period_end is None:
            raise field_error("period_start", "period_start and period_end are both required for a custom period")
        if period_start > period_end:
            raise field_error("period_end", "period_end must not be before period_start")
        return period_start, period_end

    if period_start is not None or period_end is not None:
        # Allow an explicit override even for a "computed" period type
        # (e.g. re-creating last month's budget) -- but only a matched
        # pair, never one bound without the other.
        if period_start is None or period_end is None:
            raise field_error("period_start", "give both period_start and period_end, or neither")
        return period_start, period_end

    if period == "daily":
        return today, today
    if period == "weekly":
        # Week starts Monday (the story's own rule). Monday=0 in
        # date.weekday().
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    # monthly
    start = today.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_month - timedelta(days=1)


def _to_response(budget: Budget) -> BudgetResponse:
    return BudgetResponse(
        id=str(budget.id),
        scope=BudgetScope(budget.scope).value,
        user_id=str(budget.user_id) if budget.user_id else None,
        group_id=str(budget.group_id) if budget.group_id else None,
        category_id=str(budget.category_id) if budget.category_id else None,
        period=BudgetPeriod(budget.period).value,
        period_start=budget.period_start,
        period_end=budget.period_end,
        amount_minor=budget.amount_minor,
        currency=budget.currency,
        rollover=budget.rollover,
        alert_thresholds=list(budget.alert_thresholds),
        is_active=budget.is_active,
        created_by=str(budget.created_by),
        created_at=budget.created_at,
        updated_at=budget.updated_at,
    )


def _validate_category(db: Session, category_id_raw: str | None, current_user: User) -> uuid.UUID | None:
    if category_id_raw is None:
        return None
    try:
        category_id = uuid.UUID(category_id_raw)
    except ValueError:
        raise field_error("category_id", "not a valid UUID")
    category = db.get(Category, category_id)
    if category is None or category.deleted_at is not None:
        raise field_error("category_id", "category not found")
    if not category.is_system and category.owner_user_id != current_user.id:
        raise field_error("category_id", "category not found")
    return category_id


def _visible_budget(db: Session, budget_id: uuid.UUID, current_user: User) -> Budget | None:
    budget = db.get(Budget, budget_id)
    if budget is None or budget.deleted_at is not None:
        return None
    if budget.scope == BudgetScope.USER.value:
        return budget if budget.user_id == current_user.id else None
    membership = db.execute(
        select(GroupMember).where(
            GroupMember.group_id == budget.group_id,
            GroupMember.user_id == current_user.id,
            GroupMember.removed_at.is_(None),
        )
    ).scalar_one_or_none()
    return budget if membership is not None else None


@router.post("", response_model=BudgetResponse, status_code=status.HTTP_201_CREATED)
def create_budget(
    body: BudgetCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BudgetResponse:
    if body.rollover and not _ROLLOVER_ENABLED:
        raise field_error("rollover", "rollover is not available yet")

    user_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    tz_name = current_user.timezone

    if body.scope == "user":
        user_id = current_user.id
    else:
        if not body.group_id:
            raise field_error("group_id", "group_id is required for a group-scoped budget")
        try:
            group_id = uuid.UUID(body.group_id)
        except ValueError:
            raise field_error("group_id", "not a valid UUID")
        _require_group_admin(db, group_id, current_user)  # the story's own AC: member -> 403

    category_id = _validate_category(db, body.category_id, current_user)
    period_start, period_end = _compute_period_bounds(body.period, body.period_start, body.period_end, tz_name)

    budget = Budget(
        scope=BudgetScope(body.scope),
        user_id=user_id,
        group_id=group_id,
        category_id=category_id,
        period=BudgetPeriod(body.period),
        period_start=period_start,
        period_end=period_end,
        amount_minor=body.amount_minor,
        currency=body.currency,
        rollover=body.rollover,
        alert_thresholds=body.alert_thresholds,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(budget)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(Budget).where(
                Budget.scope == BudgetScope(body.scope),
                Budget.user_id == user_id,
                Budget.group_id == group_id,
                Budget.category_id == category_id,
                Budget.period == BudgetPeriod(body.period),
                Budget.is_active.is_(True),
                Budget.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "an active budget already exists for this scope/category/period",
                "existing_budget_id": str(existing.id) if existing else None,
            },
        )
    db.refresh(budget)
    return _to_response(budget)


@router.get("", response_model=list[BudgetResponse])
def list_budgets(
    scope: str | None = Query(default=None),
    group_id: uuid.UUID | None = Query(default=None),
    period: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BudgetResponse]:
    member_group_ids = select(GroupMember.group_id).where(
        GroupMember.user_id == current_user.id, GroupMember.removed_at.is_(None)
    )
    query = select(Budget).where(
        Budget.deleted_at.is_(None),
        (Budget.user_id == current_user.id) | (Budget.group_id.in_(member_group_ids)),
    )
    if scope is not None:
        query = query.where(Budget.scope == BudgetScope(scope))
    if group_id is not None:
        query = query.where(Budget.group_id == group_id)
    if period is not None:
        query = query.where(Budget.period == BudgetPeriod(period))

    rows = db.execute(query.order_by(Budget.created_at.desc())).scalars().all()
    return [_to_response(b) for b in rows]


@router.get("/{budget_id}", response_model=BudgetResponse)
def get_budget(
    budget_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BudgetResponse:
    budget = _visible_budget(db, budget_id, current_user)
    if budget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="budget not found")
    return _to_response(budget)


@router.patch("/{budget_id}", response_model=BudgetResponse)
def update_budget(
    budget_id: uuid.UUID,
    body: BudgetUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BudgetResponse:
    budget = _visible_budget(db, budget_id, current_user)
    if budget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="budget not found")
    if budget.scope == BudgetScope.USER.value:
        if budget.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not your budget")
    else:
        # ck_budgets_scope_matches_owner guarantees group_id is set
        # whenever scope != user -- mypy can't see a DB constraint.
        assert budget.group_id is not None
        _require_group_admin(db, budget.group_id, current_user)

    update_fields = body.model_fields_set
    if "category_id" in update_fields:
        budget.category_id = _validate_category(db, body.category_id, current_user)
    if "amount_minor" in update_fields and body.amount_minor is not None:
        budget.amount_minor = body.amount_minor
    if "currency" in update_fields and body.currency is not None:
        budget.currency = body.currency
    if "rollover" in update_fields:
        if body.rollover and not _ROLLOVER_ENABLED:
            raise field_error("rollover", "rollover is not available yet")
        budget.rollover = bool(body.rollover)
    if "alert_thresholds" in update_fields and body.alert_thresholds is not None:
        budget.alert_thresholds = body.alert_thresholds
    if "is_active" in update_fields and body.is_active is not None:
        budget.is_active = body.is_active
    budget.updated_at = datetime.now(UTC)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="an active budget already exists for this scope/category/period",
        )
    db.refresh(budget)
    return _to_response(budget)


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_budget(
    budget_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    budget = _visible_budget(db, budget_id, current_user)
    if budget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="budget not found")
    if budget.scope == BudgetScope.USER.value:
        if budget.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not your budget")
    else:
        # ck_budgets_scope_matches_owner guarantees group_id is set
        # whenever scope != user -- mypy can't see a DB constraint.
        assert budget.group_id is not None
        _require_group_admin(db, budget.group_id, current_user)

    budget.deleted_at = datetime.now(UTC)
    db.commit()
