import enum
import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Enum, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GroupRole(str, enum.Enum):
    """Mirrors the real Postgres `group_role` enum (owner/admin/member,
    Story 0.2). Values are ordered least-to-most-privileged in
    `_ROLE_RANK` (app.core.security) -- Story 0.4's own
    `require_group_role(minimum_role)` compares against that rank, not
    against this class's declaration order (a plain Python Enum has no
    built-in ordering)."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class GroupMember(Base):
    """Maps the real `group_members` table (Story 0.2). A member is
    "active" iff `removed_at IS NULL` -- there is no separate boolean;
    that's the real, single source of truth this whole file (and
    app.core.security) treats as authoritative."""

    __tablename__ = "group_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[GroupRole] = mapped_column(
        Enum(
            "owner", "admin", "member",
            name="group_role",
            create_type=False,  # the type already exists -- Story 0.2's migration created it
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    removed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    @property
    def is_active(self) -> bool:
        return self.removed_at is None
