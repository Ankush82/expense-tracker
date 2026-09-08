"""Story 3.1: expense CRUD. Every mutating route is authorized through
app.core.security's visibility_filter for existence/visibility (a caller
who can't see an expense gets 404, never 403 -- the story's own AC), and
this router's own strict "only the owner may edit or delete" rule for
mutation (deliberately NOT going through get_editable_expense_fields()'s
group-admin/group_id-only carve-out from Story 0.4 -- that carve-out is
for a future, separate group-management action, not this endpoint; see
the docstring on update_expense).

Epic 15's split model does not exist yet, so the story's "editing an
expense that is part of a settled split is blocked with 409" rule can't
be implemented for real here -- there is nothing in the schema to check
against. Left as a known gap for whichever story adds splits."""
from __future__ import annotations

import base64
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.activity import record
from app.core.db import get_db
from app.core.security import get_current_user, visibility_filter
from app.models.audit_log import AuditLog
from app.models.category import Category
from app.models.expense import Expense, ExpenseSource, ExpenseStatus
from app.models.group_member import GroupMember
from app.models.user import User
from app.schemas.expenses import (
    BulkExpenseCreateRequest,
    BulkExpenseFailure,
    BulkExpenseResponse,
    ExpenseCreate,
    ExpenseListResponse,
    ExpenseResponse,
    ExpenseUpdate,
    field_error,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])

_IDEMPOTENCY_WINDOW = timedelta(hours=24)
# The fields whose change requires an audit_log entry (the story's own
# list: "amount, merchant, category or date").
_AUDITED_COLUMNS = frozenset({"amount_minor", "merchant_raw", "category_id", "occurred_at"})


def _to_response(expense: Expense) -> ExpenseResponse:
    return ExpenseResponse(
        id=str(expense.id),
        user_id=str(expense.user_id),
        group_id=str(expense.group_id) if expense.group_id else None,
        amount_minor=expense.amount_minor,
        currency=expense.currency,
        merchant=expense.merchant_raw,
        category_id=str(expense.category_id) if expense.category_id else None,
        occurred_at=expense.occurred_at,
        notes=expense.notes,
        source=ExpenseSource(expense.source).value,
        status=ExpenseStatus(expense.status).value,
        created_at=expense.created_at,
        updated_at=expense.updated_at,
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
        # Never distinguish "exists but isn't yours" from "doesn't exist"
        # for a category any more than for an expense.
        raise field_error("category_id", "category not found")
    return category_id


def _validate_group(db: Session, group_id_raw: str | None, current_user: User) -> uuid.UUID | None:
    if group_id_raw is None:
        return None
    try:
        group_id = uuid.UUID(group_id_raw)
    except ValueError:
        raise field_error("group_id", "not a valid UUID")
    membership = db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == current_user.id,
            GroupMember.removed_at.is_(None),
        )
    ).scalar_one_or_none()
    if membership is None:
        raise field_error("group_id", "caller is not an active member of this group")
    return group_id


def _visible_expense(db: Session, expense_id: uuid.UUID, current_user: User) -> Expense | None:
    query = visibility_filter(
        select(Expense).where(Expense.id == expense_id, Expense.deleted_at.is_(None)),
        current_user,
        db,
    )
    return db.execute(query).scalar_one_or_none()


@router.post("", response_model=ExpenseResponse, status_code=status.HTTP_200_OK)
def create_expense(
    body: ExpenseCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExpenseResponse:
    """A repeat POST with the same Idempotency-Key within 24h returns the
    original expense (still 200 -- this endpoint never returns 201, by
    the story's own AC: "two 200s"), rather than creating a second row.
    Not race-safe against two truly concurrent requests with the same
    key (no DB-level uniqueness backs this -- see the migration's own
    docstring for why a permanent unique constraint would be wrong
    here); acceptable for this story's actual use case (a flaky mobile
    retry, not a high-concurrency financial ledger)."""
    if idempotency_key:
        cutoff = datetime.now(UTC) - _IDEMPOTENCY_WINDOW
        existing = db.execute(
            select(Expense)
            .where(
                Expense.user_id == current_user.id,
                Expense.idempotency_key == idempotency_key,
                Expense.deleted_at.is_(None),
                Expense.created_at >= cutoff,
            )
            .order_by(Expense.created_at.desc())
        ).scalars().first()
        if existing is not None:
            return _to_response(existing)

    category_id = _validate_category(db, body.category_id, current_user)
    group_id = _validate_group(db, body.group_id, current_user)

    expense = Expense(
        user_id=current_user.id,
        group_id=group_id,
        amount_minor=body.amount_minor,
        currency=body.currency,
        merchant_raw=body.merchant,
        category_id=category_id,
        occurred_at=body.occurred_at,
        notes=body.notes,
        source=ExpenseSource.MANUAL,
        status=ExpenseStatus.CONFIRMED,
        idempotency_key=idempotency_key,
    )
    db.add(expense)
    db.flush()  # real expense.id, needed for the activity event's entity_id, before commit
    if group_id is not None:
        # Story 9.1: only a GROUP expense has a group feed to appear in
        # -- a personal expense (group_id is None) has nothing to log.
        record(
            db,
            group_id=group_id,
            actor_user_id=current_user.id,
            event_type="expense_added",
            entity_type="expense",
            entity_id=str(expense.id),
            metadata={"merchant": expense.merchant_raw, "amount_minor": expense.amount_minor, "currency": expense.currency},
        )
    db.commit()
    db.refresh(expense)
    return _to_response(expense)


@router.post("/bulk", response_model=BulkExpenseResponse)
def create_bulk_expenses(
    body: BulkExpenseCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkExpenseResponse:
    """Story 3.3: up to 100 rows in one call. Each row is validated and
    persisted independently -- one bad row (row 7 has a bad amount, the
    story's own example) never blocks its siblings; `failed` carries
    exactly which rows and why, `created` carries everything that made
    it in. No idempotency handling here (unlike the single POST) -- the
    story doesn't ask for it, and a spreadsheet paste is a one-shot
    action, not a flaky-mobile-retry scenario.

    Category/group membership are checked against ONE pre-fetched set
    per request, not one query per row -- the story's own AC ("a 100-row
    batch completes in under 3 seconds server-side") is a real N+1-query
    trap otherwise."""
    parsed: dict[int, ExpenseCreate] = {}
    failed: list[BulkExpenseFailure] = []

    for index, raw_item in enumerate(body.items):
        try:
            parsed[index] = ExpenseCreate(**raw_item)
        except ValidationError as exc:
            failed.append(
                BulkExpenseFailure(
                    index=index,
                    errors=[
                        {"loc": ["body", *[str(p) for p in e["loc"]]], "msg": e["msg"], "type": e["type"]}
                        for e in exc.errors()
                    ],
                )
            )

    category_ids: set[uuid.UUID] = set()
    group_ids: set[uuid.UUID] = set()
    for item in parsed.values():
        if item.category_id:
            try:
                category_ids.add(uuid.UUID(item.category_id))
            except ValueError:
                pass  # caught again, as a real per-row error, in the loop below
        if item.group_id:
            try:
                group_ids.add(uuid.UUID(item.group_id))
            except ValueError:
                pass

    categories_by_id: dict[uuid.UUID, Category] = {}
    if category_ids:
        for fetched_category in db.execute(select(Category).where(Category.id.in_(category_ids))).scalars():
            categories_by_id[fetched_category.id] = fetched_category

    member_group_ids: set[uuid.UUID] = set()
    if group_ids:
        member_group_ids = {
            m.group_id
            for m in db.execute(
                select(GroupMember).where(
                    GroupMember.group_id.in_(group_ids),
                    GroupMember.user_id == current_user.id,
                    GroupMember.removed_at.is_(None),
                )
            ).scalars()
        }

    created_expenses: list[Expense] = []
    for index, item in parsed.items():
        row_errors: list[dict] = []
        resolved_category_id: uuid.UUID | None = None
        if item.category_id:
            try:
                category_uuid = uuid.UUID(item.category_id)
            except ValueError:
                row_errors.append({"loc": ["body", "category_id"], "msg": "not a valid UUID", "type": "value_error"})
            else:
                category = categories_by_id.get(category_uuid)
                visible = category is not None and category.deleted_at is None and (
                    category.is_system or category.owner_user_id == current_user.id
                )
                if not visible:
                    row_errors.append({"loc": ["body", "category_id"], "msg": "category not found", "type": "value_error"})
                else:
                    resolved_category_id = category_uuid

        resolved_group_id: uuid.UUID | None = None
        if item.group_id:
            try:
                group_uuid = uuid.UUID(item.group_id)
            except ValueError:
                row_errors.append({"loc": ["body", "group_id"], "msg": "not a valid UUID", "type": "value_error"})
            else:
                if group_uuid not in member_group_ids:
                    row_errors.append(
                        {
                            "loc": ["body", "group_id"],
                            "msg": "caller is not an active member of this group",
                            "type": "value_error",
                        }
                    )
                else:
                    resolved_group_id = group_uuid

        if row_errors:
            failed.append(BulkExpenseFailure(index=index, errors=row_errors))
            continue

        expense = Expense(
            user_id=current_user.id,
            group_id=resolved_group_id,
            amount_minor=item.amount_minor,
            currency=item.currency,
            merchant_raw=item.merchant,
            category_id=resolved_category_id,
            occurred_at=item.occurred_at,
            notes=item.notes,
            source=ExpenseSource.MANUAL,
            status=ExpenseStatus.CONFIRMED,
        )
        db.add(expense)
        created_expenses.append(expense)

    db.commit()
    for expense in created_expenses:
        db.refresh(expense)

    failed.sort(key=lambda f: f.index)
    return BulkExpenseResponse(created=[_to_response(e) for e in created_expenses], failed=failed)


@router.get("/{expense_id}", response_model=ExpenseResponse)
def get_expense(
    expense_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExpenseResponse:
    expense = _visible_expense(db, expense_id, current_user)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="expense not found")
    return _to_response(expense)


@router.patch("/{expense_id}", response_model=ExpenseResponse)
def update_expense(
    expense_id: uuid.UUID,
    body: ExpenseUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExpenseResponse:
    """Visibility is checked first, so a caller who can't see the expense
    at all gets the identical 404 a nonexistent id would (never leaks
    existence via a 403). Ownership is checked second and separately:
    "Only the expense owner may edit it. Group admins may not" is this
    story's own, stricter rule than Story 0.4's general
    get_editable_expense_fields() carve-out (which lets a group
    admin/owner change just an expense's group_id) -- that carve-out is
    reserved for a future group-management action, never exercised
    through this general CRUD endpoint."""
    expense = _visible_expense(db, expense_id, current_user)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="expense not found")
    if expense.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="only the expense owner may edit it")

    update_fields = body.model_fields_set
    before: dict[str, str] = {}
    after: dict[str, str] = {}

    if "category_id" in update_fields:
        new_category_id = _validate_category(db, body.category_id, current_user)
        if new_category_id != expense.category_id:
            before["category_id"] = str(expense.category_id)
            after["category_id"] = str(new_category_id)
        expense.category_id = new_category_id
    if "group_id" in update_fields:
        expense.group_id = _validate_group(db, body.group_id, current_user)

    for field_name, column_name in (
        ("amount_minor", "amount_minor"),
        ("currency", "currency"),
        ("merchant", "merchant_raw"),
        ("occurred_at", "occurred_at"),
        ("notes", "notes"),
    ):
        if field_name not in update_fields:
            continue
        new_value = getattr(body, field_name)
        old_value = getattr(expense, column_name)
        if column_name in _AUDITED_COLUMNS and old_value != new_value:
            before[column_name] = str(old_value)
            after[column_name] = str(new_value)
        setattr(expense, column_name, new_value)

    expense.updated_at = datetime.now(UTC)

    if before:
        db.add(
            AuditLog(
                actor_user_id=current_user.id,
                entity_type="expense",
                entity_id=str(expense.id),
                action="update",
                before=before,
                after=after,
            )
        )
        if expense.group_id is not None:
            record(
                db,
                group_id=expense.group_id,
                actor_user_id=current_user.id,
                event_type="expense_edited",
                entity_type="expense",
                entity_id=str(expense.id),
                metadata={"changed_fields": sorted(before.keys())},
            )

    db.commit()
    db.refresh(expense)
    return _to_response(expense)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_expense(
    expense_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Soft delete only -- deleted_at is set, the row survives. Excluded
    from lists/totals (every query in this router filters
    deleted_at IS NULL) and from a future GET by id (treated the same as
    never having existed, consistent with how visibility already works)."""
    expense = _visible_expense(db, expense_id, current_user)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="expense not found")
    if expense.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="only the expense owner may delete it")

    expense.deleted_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor_user_id=current_user.id,
            entity_type="expense",
            entity_id=str(expense.id),
            action="delete",
            before=None,
            after=None,
        )
    )
    if expense.group_id is not None:
        record(
            db,
            group_id=expense.group_id,
            actor_user_id=current_user.id,
            event_type="expense_deleted",
            entity_type="expense",
            entity_id=str(expense.id),
            metadata=None,
        )
    db.commit()


def _encode_cursor(occurred_at: datetime, expense_id: uuid.UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{expense_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        occurred_at_raw, id_raw = raw.split("|", 1)
        return datetime.fromisoformat(occurred_at_raw), uuid.UUID(id_raw)
    except Exception as exc:
        raise field_error("cursor", "invalid cursor") from exc


@router.get("", response_model=ExpenseListResponse)
def list_expenses(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    group_id: uuid.UUID | None = Query(default=None),
    category_id: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExpenseListResponse:
    """Keyset (not offset) pagination: `cursor` opaquely encodes the last
    row's (occurred_at, id) from the previous page, ordered occurred_at
    DESC then id DESC as a tiebreaker -- stable even if new expenses are
    inserted between page fetches, unlike an offset would be."""
    query = select(Expense).where(Expense.deleted_at.is_(None))
    query = visibility_filter(query, current_user, db)

    if from_ is not None:
        query = query.where(Expense.occurred_at >= datetime.combine(from_, datetime.min.time(), tzinfo=UTC))
    if to is not None:
        query = query.where(Expense.occurred_at <= datetime.combine(to, datetime.max.time(), tzinfo=UTC))
    if group_id is not None:
        query = query.where(Expense.group_id == group_id)
    if category_id is not None:
        query = query.where(Expense.category_id == category_id)

    if cursor is not None:
        cursor_occurred_at, cursor_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                Expense.occurred_at < cursor_occurred_at,
                and_(Expense.occurred_at == cursor_occurred_at, Expense.id < cursor_id),
            )
        )

    query = query.order_by(Expense.occurred_at.desc(), Expense.id.desc()).limit(limit + 1)
    rows = list(db.execute(query).scalars().all())

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _encode_cursor(last.occurred_at, last.id)

    return ExpenseListResponse(items=[_to_response(r) for r in rows], next_cursor=next_cursor)
