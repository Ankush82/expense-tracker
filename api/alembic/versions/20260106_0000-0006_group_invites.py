"""group_invites -- Story 2.2

Revision ID: 0006_group_invites
Revises: 0005_group_name_unique
Create Date: 2026-01-06 00:00:00

Story 2.2's own DATA section specifies: id, group_id, invited_by, email,
token_hash, role, expires_at, accepted_at, accepted_by, revoked_at,
max_uses, use_count. Two columns beyond that literal list are added here,
both required by a business rule the story states but gives no column
for: "Re-inviting an email with a live pending invite... is rate limited
to 3 sends per email per group per day" needs somewhere to track how many
times, and how recently, a given invite has been (re)sent --
`send_count` and `last_sent_at`.

Only the raw token is ever shareable (in the invite URL); token_hash is
what's actually stored, per the story's own security requirement
("Tokens never appear in server logs..."). role reuses the same
group_role enum groups/group_members already use, restricted at the
application layer to admin/member (an invite can never grant owner).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Real bug, found live (2026-09-09): sa.Enum(..., create_type=False)
# still tried to CREATE TYPE group_role here and failed with
# DuplicateObject -- the enum was already created by 0001's own
# migration and create_type=False did not suppress it inside
# op.create_table(). postgresql.ENUM(name=..., create_type=False), with
# no values list (this migration isn't defining the type, only
# referencing an existing one by name), is the real, working way to
# reference an existing Postgres enum type from a later migration.
group_role_ref = postgresql.ENUM(name="group_role", create_type=False)

revision: str = "0006_group_invites"
down_revision: Union[str, None] = "0005_group_name_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "group_invites",
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
            "invited_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("role", group_role_ref, nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "accepted_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Not in the story's own DATA list -- see module docstring: needed
        # for the "3 sends per email per group per day" rate limit, which
        # has nowhere else to be tracked.
        sa.Column("send_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_group_invites_group_id", "group_invites", ["group_id"])
    # A real, live pending (not accepted, not revoked, not expired) email
    # invite must be found by (group_id, email) to implement "re-inviting
    # an email... resends rather than creating a duplicate row" --
    # queried, not uniquely constrained (a group can have many PAST,
    # accepted/revoked/expired invites for the same email over time).
    op.create_index(
        "ix_group_invites_group_id_email",
        "group_invites",
        ["group_id", "email"],
        postgresql_where=sa.text("email IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_group_invites_group_id_email", table_name="group_invites")
    op.drop_index("ix_group_invites_group_id", table_name="group_invites")
    op.drop_table("group_invites")
