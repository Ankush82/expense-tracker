"""Story 3.4: category CRUD plus per-user hide/unhide of a system
category. The story's own ENDPOINTS section lists GET/POST/PATCH/DELETE
on /categories only, but its BUSINESS RULES section requires "a user can
hide" a system category -- there is no way to express that through
PATCH (which is explicitly "custom only"), so this router adds two
small, dedicated routes for it (POST/DELETE /categories/{id}/hide) not
literally enumerated in the story text but required to satisfy it.

"Category picker groups system and custom categories under clear
headings" (the story's third AC) is a frontend concern -- nothing here
implements UI; GET /categories returns `is_system` on every row so a
future frontend can group by it."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import field_error
from app.core.security import get_current_user
from app.models.category import Category
from app.models.expense import Expense
from app.models.hidden_category import HiddenCategory
from app.models.user import User
from app.schemas.categories import CategoryCreate, CategoryResponse, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["categories"])

_MAX_CUSTOM_CATEGORIES_PER_USER = 50


def _to_response(category: Category, *, hidden: bool) -> CategoryResponse:
    return CategoryResponse(
        id=str(category.id),
        name=category.name,
        icon=category.icon,
        color=category.color,
        is_system=category.is_system,
        parent_id=str(category.parent_id) if category.parent_id else None,
        owner_user_id=str(category.owner_user_id) if category.owner_user_id else None,
        hidden=hidden,
    )


def _visible_category_or_404(db: Session, category_id: uuid.UUID, current_user: User) -> Category:
    """A category is visible to a caller iff it's a system category or
    one they own -- never another user's custom category. Mirrors
    app.core.security.visibility_filter's own "404, never 403" rule:
    a category outside that set doesn't exist as far as this caller is
    concerned."""
    category = db.get(Category, category_id)
    if (
        category is None
        or category.deleted_at is not None
        or not (category.is_system or category.owner_user_id == current_user.id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="category not found")
    return category


def _validate_parent(db: Session, parent_id_raw: str | None, current_user: User) -> uuid.UUID | None:
    """One level of nesting only: the parent itself must have no parent
    of its own. The parent must be visible to the caller (system or
    their own) -- same rule as any other category reference."""
    if parent_id_raw is None:
        return None
    try:
        parent_id = uuid.UUID(parent_id_raw)
    except ValueError:
        raise field_error("parent_id", "not a valid UUID")
    parent = db.get(Category, parent_id)
    if (
        parent is None
        or parent.deleted_at is not None
        or not (parent.is_system or parent.owner_user_id == current_user.id)
    ):
        raise field_error("parent_id", "category not found")
    if parent.parent_id is not None:
        raise field_error("parent_id", "only one level of nesting is allowed")
    return parent_id


@router.get("", response_model=list[CategoryResponse])
def list_categories(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CategoryResponse]:
    """System categories + the caller's own custom ones, not deleted.
    Hidden system categories are still returned (with `hidden=true`) so
    a settings screen can offer to un-hide them -- only a picker (a
    frontend concern) is expected to actually filter them out."""
    rows = db.execute(
        select(Category).where(
            Category.deleted_at.is_(None),
            (Category.is_system.is_(True)) | (Category.owner_user_id == current_user.id),
        )
    ).scalars().all()

    hidden_ids = {
        row.category_id
        for row in db.execute(
            select(HiddenCategory).where(HiddenCategory.user_id == current_user.id)
        ).scalars()
    }

    return [_to_response(c, hidden=c.id in hidden_ids) for c in rows]


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    body: CategoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CategoryResponse:
    existing_count = db.execute(
        select(func.count())
        .select_from(Category)
        .where(Category.owner_user_id == current_user.id, Category.deleted_at.is_(None))
    ).scalar_one()
    if existing_count >= _MAX_CUSTOM_CATEGORIES_PER_USER:
        raise field_error("name", f"at most {_MAX_CUSTOM_CATEGORIES_PER_USER} custom categories are allowed")

    parent_id = _validate_parent(db, body.parent_id, current_user)

    category = Category(
        owner_user_id=current_user.id,
        name=body.name,
        icon=body.icon,
        color=body.color,
        is_system=False,
        parent_id=parent_id,
    )
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # The DB's own partial unique index (owner_user_id, lower(name))
        # WHERE deleted_at IS NULL is what actually enforces "name
        # uniqueness is case-insensitive within a user's own set".
        raise field_error("name", "a category with this name already exists")
    db.refresh(category)
    return _to_response(category, hidden=False)


@router.patch("/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: uuid.UUID,
    body: CategoryUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CategoryResponse:
    # _visible_category_or_404 already restricts visibility to system
    # categories or ones current_user owns -- a stranger's custom
    # category is a 404 there, never reaching this function's body, so
    # the only real gate left to check here is is_system.
    category = _visible_category_or_404(db, category_id, current_user)
    if category.is_system:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="system categories cannot be edited")

    update_fields = body.model_fields_set
    if "name" in update_fields:
        if body.name is None:
            # name is NOT NULL at the DB level -- reject explicitly here
            # rather than let an IntegrityError from the commit below
            # get mistaken for the name-uniqueness violation it also
            # catches.
            raise field_error("name", "name cannot be null")
        category.name = body.name
    if "icon" in update_fields:
        category.icon = body.icon
    if "color" in update_fields:
        category.color = body.color
    if "parent_id" in update_fields:
        category.parent_id = _validate_parent(db, body.parent_id, current_user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise field_error("name", "a category with this name already exists")
    db.refresh(category)
    return _to_response(category, hidden=False)


@router.delete("/{category_id}", response_model=CategoryResponse)
def delete_category(
    category_id: uuid.UUID,
    reassign_to: str = Query(..., description="category id to move this category's expenses to"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CategoryResponse:
    """Custom categories only -- a system category 403s (the story's
    own AC, verbatim). `reassign_to` is required, not optional: the
    story's own text is "requires a target category to move its
    expenses to", with no carve-out for a category that happens to have
    zero expenses today. The move and the soft-delete happen in the
    same DB transaction (one commit) -- the story's own AC: "moves all
    30 and leaves no orphans". As in update_category, a stranger's
    custom category is already a 404 from _visible_category_or_404."""
    category = _visible_category_or_404(db, category_id, current_user)
    if category.is_system:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="system categories cannot be deleted")

    try:
        reassign_to_id = uuid.UUID(reassign_to)
    except ValueError:
        raise field_error("reassign_to", "not a valid UUID")
    if reassign_to_id == category.id:
        raise field_error("reassign_to", "cannot reassign a category's expenses to itself")
    target = _visible_category_or_404(db, reassign_to_id, current_user)

    db.execute(
        update(Expense)
        .where(Expense.category_id == category.id)
        .values(category_id=target.id, updated_at=datetime.now(UTC))
    )
    category.deleted_at = datetime.now(UTC)
    db.commit()
    db.refresh(category)
    return _to_response(category, hidden=False)


@router.post("/{category_id}/hide", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def hide_category(
    category_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    category = _visible_category_or_404(db, category_id, current_user)
    if not category.is_system:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="only a system category can be hidden -- delete a custom category instead",
        )

    existing = db.execute(
        select(HiddenCategory).where(
            HiddenCategory.user_id == current_user.id, HiddenCategory.category_id == category.id
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(HiddenCategory(user_id=current_user.id, category_id=category.id))
        db.commit()


@router.delete("/{category_id}/hide", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def unhide_category(
    category_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    category = _visible_category_or_404(db, category_id, current_user)
    db.execute(
        delete(HiddenCategory).where(
            HiddenCategory.user_id == current_user.id, HiddenCategory.category_id == category.id
        )
    )
    db.commit()
