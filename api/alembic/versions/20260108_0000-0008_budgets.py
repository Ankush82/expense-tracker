"""budgets -- Story 6.1

Revision ID: 0008_budgets
Revises: 0007_member_visibility
Create Date: 2026-01-08 00:00:00

Story 6.1's own DATA section, verbatim: id, scope enum(user,group),
user_id fk null, group_id fk null, category_id fk null, period
enum(daily,weekly,monthly,custom), period_start date null, period_end
date null, amount_minor bigint, currency char(3), rollover bool default
false, alert_thresholds int[] default '{80,100}', is_active bool,
created_by fk, created_at, updated_at, deleted_at null.

Two real, DB-level constraints beyond what a plain column list gives you
(the story's own AC: "Constraint violation (both user_id and group_id
set) is rejected at DB level, not only in application code"):
  - ck_budgets_scope_matches_owner: exactly one of user_id/group_id is
    non-null, and it matches `scope`.
  - uq_budgets_one_active_per_scope_category_period: "a user may have
    one active budget per (scope, category, period)" -- a partial
    unique index on the real identifying columns WHERE is_active AND
    NOT deleted, using COALESCE so two NULLs (category_id for an
    "overall" budget, or whichever of user_id/group_id is unused) are
    still treated as equal for uniqueness purposes -- plain SQL NULL
    never equals NULL, which would otherwise let unlimited "overall"
    budgets (category_id IS NULL) coexist for the same scope/period.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_budgets"
down_revision: Union[str, None] = "0007_member_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE budget_scope AS ENUM ('user', 'group');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE budget_period AS ENUM ('daily', 'weekly', 'monthly', 'custom');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    op.create_table(
        "budgets",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "scope",
            sa.dialects.postgresql.ENUM(name="budget_scope", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "category_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "period",
            sa.dialects.postgresql.ENUM(name="budget_period", create_type=False),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("rollover", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "alert_thresholds",
            sa.dialects.postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{80,100}'"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("amount_minor > 0", name="ck_budgets_amount_minor_positive"),
        sa.CheckConstraint(
            "(scope = 'user' AND user_id IS NOT NULL AND group_id IS NULL) OR "
            "(scope = 'group' AND group_id IS NOT NULL AND user_id IS NULL)",
            name="ck_budgets_scope_matches_owner",
        ),
    )
    op.create_index("ix_budgets_user_id", "budgets", ["user_id"])
    op.create_index("ix_budgets_group_id", "budgets", ["group_id"])
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_budgets_one_active_per_scope_category_period
        ON budgets (
            scope,
            COALESCE(user_id, '{_NIL_UUID}'),
            COALESCE(group_id, '{_NIL_UUID}'),
            COALESCE(category_id, '{_NIL_UUID}'),
            period
        )
        WHERE is_active AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_budgets_one_active_per_scope_category_period")
    op.drop_index("ix_budgets_group_id", table_name="budgets")
    op.drop_index("ix_budgets_user_id", table_name="budgets")
    op.drop_table("budgets")
    op.execute("DROP TYPE IF EXISTS budget_period")
    op.execute("DROP TYPE IF EXISTS budget_scope")
