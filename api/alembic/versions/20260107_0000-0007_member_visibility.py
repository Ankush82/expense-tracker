"""member_visibility -- Story 11.1

Revision ID: 0007_member_visibility
Revises: 0006_group_invites
Create Date: 2026-01-07 00:00:00

Story 11.1's own DATA section, verbatim: id, group_id fk, user_id fk,
level enum(full, aggregate, hidden) default 'aggregate', hide_categories
uuid[] default '{}', updated_at, unique(group_id, user_id).

No row is required to exist for a member to have a real, defined
setting -- the story's own rule ("Default for new members is
aggregate") is enforced at the APPLICATION layer (GET returns the
default when no row exists, rather than every group-join flow needing
to remember to insert one) instead of relying on group-membership
creation to always insert a matching row here. The server_default on
`level` is still real and correct for the one real place a row DOES get
written without an explicit level (none today, but the column-level
default is the right, defensive choice regardless).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Real bug, found live (2026-09-09): sa.Enum("full", "aggregate",
# "hidden", name="visibility_level", create_type=False) -- WITH the
# values list -- still emitted a bare CREATE TYPE from inside
# op.create_table() and raised DuplicateObject, exactly the same
# failure this file's own explicit DO $$ block below was written to
# avoid. postgresql.ENUM(name=..., create_type=False) with NO values
# list (a pure reference to an existing type, the same pattern
# 0006_group_invites already uses for group_role) is what actually
# works -- passing the values list appears to make SQLAlchemy think it
# owns defining the type regardless of create_type.
visibility_level_ref = postgresql.ENUM(name="visibility_level", create_type=False)

revision: str = "0007_member_visibility"
down_revision: Union[str, None] = "0006_group_invites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Real bug, found live (2026-09-09): sa.Enum(...).create(bind,
    # checkfirst=True) reproducibly emitted a bare CREATE TYPE and
    # raised DuplicateObject even on a genuinely first-ever run against
    # a database that provably had no visibility_level type yet
    # (verified directly via psql immediately before and after the
    # failure) -- checkfirst appears not to route through a real
    # existence check for a plain Enum's direct .create() call against
    # postgres. Postgres itself has no CREATE TYPE IF NOT EXISTS, so the
    # standard, bulletproof idiom (DO $$ ... EXCEPTION WHEN
    # duplicate_object) is used instead of trusting SQLAlchemy's own
    # checkfirst here.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE visibility_level AS ENUM ('full', 'aggregate', 'hidden');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.create_table(
        "member_visibility",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "level",
            visibility_level_ref,
            nullable=False,
            server_default=sa.text("'aggregate'"),
        ),
        sa.Column(
            "hide_categories",
            sa.dialects.postgresql.ARRAY(sa.dialects.postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "uq_member_visibility_group_user",
        "member_visibility",
        ["group_id", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_member_visibility_group_user", table_name="member_visibility")
    op.drop_table("member_visibility")
    op.execute("DROP TYPE IF EXISTS visibility_level")
