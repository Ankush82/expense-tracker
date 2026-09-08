"""groups.name unique per creator (case-insensitive) -- Story 2.1

Revision ID: 0005_group_name_unique
Revises: 0004_fix_category_update_trigger
Create Date: 2026-01-05 00:00:00

Story 2.1's own business rule: "Group name: 2-60 characters, must be
unique per creator, trimmed." Same real pattern Story 0.2's own migration
already used for categories (uq_categories_owner_lower_name): a partial
unique index on (created_by, lower(name)) WHERE deleted_at IS NULL, so a
soft-deleted group's name doesn't permanently block the creator from
reusing it, and the uniqueness is case-insensitive ("Flatmates" and
"flatmates" collide) since the story doesn't ask for anything finer.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0005_group_name_unique"
down_revision: Union[str, None] = "0004_fix_category_update_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_groups_creator_lower_name
        ON groups (created_by, lower(name))
        WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_groups_creator_lower_name")
