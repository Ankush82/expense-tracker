"""fix reject_system_category_change() to not silently discard UPDATEs

Revision ID: 0004_fix_category_update_trigger
Revises: 0003_hidden_categories
Create Date: 2026-01-04 01:00:00

Real bug found while implementing Story 3.4 (filed as GitHub issue #75):
Story 0.2's original trigger function unconditionally `RETURN OLD`. For
the DELETE trigger that's correct (it's what lets a non-system delete
proceed); for the UPDATE trigger it's wrong -- a BEFORE UPDATE trigger
writes back whatever row it returns, so returning OLD instead of NEW
means every legitimate UPDATE to a non-system category silently writes
the PRE-update values back, with no exception and no error. Story 3.4's
own PATCH /categories/{id} (rename/re-icon/re-color a custom category)
was completely inert until this fix -- confirmed via a real Postgres
UPDATE that returned success while leaving the row unchanged.

The fix branches on TG_OP: keep OLD for a DELETE, return NEW (after
the same is_system check) for an UPDATE so the write actually lands.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0004_fix_category_update_trigger"
down_revision: Union[str, None] = "0003_hidden_categories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_FUNCTION = """
    CREATE OR REPLACE FUNCTION reject_system_category_change()
    RETURNS TRIGGER AS $$
    BEGIN
        IF OLD.is_system = TRUE THEN
            RAISE EXCEPTION
                'system category (id=%) is immutable', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END;
    $$ LANGUAGE plpgsql;
"""

_NEW_FUNCTION = """
    CREATE OR REPLACE FUNCTION reject_system_category_change()
    RETURNS TRIGGER AS $$
    BEGIN
        IF OLD.is_system = TRUE THEN
            RAISE EXCEPTION
                'system category (id=%) is immutable', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_NEW_FUNCTION)


def downgrade() -> None:
    op.execute(_OLD_FUNCTION)
