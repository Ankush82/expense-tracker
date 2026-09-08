"""hidden_categories for Story 3.4's "user can hide a system category"

Revision ID: 0003_hidden_categories
Revises: 0002_expense_idempotency_key
Create Date: 2026-01-04 00:00:00

Story 3.4's own text: "System categories cannot be edited or deleted,
but a user can hide one; hidden categories stop appearing in pickers
while existing expenses keep them." That's a per-user visibility flag
layered on top of a category the user does not own, so it can't live
as a column on `categories` itself (which is shared across every
user) -- it needs its own per-(user, category) row.

Hiding a CUSTOM category makes no sense (the user would just delete
it) -- the app layer (app.routers.categories) only ever writes rows
here for is_system=true categories, but nothing at the DB level
enforces that; it's an application-level rule, same as several other
cross-table checks in this codebase.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_hidden_categories"
down_revision: Union[str, None] = "0002_expense_idempotency_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hidden_categories",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "category_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "uq_hidden_categories_user_category",
        "hidden_categories",
        ["user_id", "category_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_hidden_categories_user_category", table_name="hidden_categories")
    op.drop_table("hidden_categories")
