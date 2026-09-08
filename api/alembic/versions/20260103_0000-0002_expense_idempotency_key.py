"""expenses.idempotency_key for Story 3.1's idempotent POST /expenses

Revision ID: 0002_expense_idempotency_key
Revises: 0001_initial_schema
Create Date: 2026-01-03 00:00:00

Story 3.1's own text: "A repeat within 24 hours returns the original
expense rather than creating a second one." That's a time-WINDOWED
rule, not a permanent one -- the same key text is free to be reused
for an unrelated new expense once the original is more than 24h old.
That rules out a plain unique constraint on (user_id, idempotency_key)
(it would permanently block reuse of a key string); the 24h window
itself is enforced in application code (app.routers.expenses), and
this index only makes that lookup fast, not correct on its own.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_expense_idempotency_key"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("expenses", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.create_index(
        "ix_expenses_user_id_idempotency_key",
        "expenses",
        ["user_id", "idempotency_key"],
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_expenses_user_id_idempotency_key", table_name="expenses")
    op.drop_column("expenses", "idempotency_key")
