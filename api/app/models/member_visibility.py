import enum
import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Enum, ForeignKey, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VisibilityLevel(str, enum.Enum):
    """Mirrors the real Postgres `visibility_level` enum (Story 11.1).
    Order here is NOT a privacy ranking to compare via rank (unlike
    GroupRole) -- each level is a distinct, real sharing mode, not a
    point on a single "more/less privileged" scale."""

    FULL = "full"
    AGGREGATE = "aggregate"
    HIDDEN = "hidden"


class MemberVisibility(Base):
    """Maps the real `member_visibility` table (Story 11.1). Absence of
    a row for a given (group_id, user_id) is a REAL, meaningful state --
    "this member hasn't changed anything, use the default (aggregate)"
    -- not an error; see app.routers.visibility's own GET for where that
    default is applied. `hide_categories` lets a user exclude specific
    categories from group visibility even at level=full."""

    __tablename__ = "member_visibility"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[VisibilityLevel] = mapped_column(
        Enum(
            "full", "aggregate", "hidden",
            name="visibility_level",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        server_default=text("'aggregate'"),
    )
    hide_categories: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=text("'{}'")
    )
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
