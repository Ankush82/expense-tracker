"""group_activity -- Story 9.1

Revision ID: 0009_group_activity
Revises: 0008_budgets
Create Date: 2026-01-09 00:00:00

Story 9.1's own DATA section, verbatim: id bigserial, group_id fk,
actor_user_id fk, event_type, entity_type, entity_id, metadata jsonb,
created_at.

event_type/entity_type are plain text, not a Postgres enum -- the
story's own EVENT TYPES list is explicitly versioned ("v1"), and new
event types will be added by future stories (splits, comments, more of
Epic 9 itself) without each one needing its own migration to widen an
enum. Validated at the application layer instead (app.core.activity's own
_KNOWN_EVENT_TYPES).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_group_activity"
down_revision: Union[str, None] = "0008_budgets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "group_activity",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Reverse-chronological cursor pagination (the endpoint's own real
    # requirement) reads this index directly -- id is already
    # monotonically increasing (bigserial), so ORDER BY id DESC needs no
    # separate created_at sort key or tiebreaker.
    op.create_index("ix_group_activity_group_id_id", "group_activity", ["group_id", sa.text("id DESC")])


def downgrade() -> None:
    op.drop_index("ix_group_activity_group_id_id", table_name="group_activity")
    op.drop_table("group_activity")
