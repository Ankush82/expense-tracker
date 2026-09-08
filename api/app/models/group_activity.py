import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, BigInteger, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GroupActivity(Base):
    """Maps the real `group_activity` table (Story 9.1). An append-only
    event log -- nothing here is ever updated or deleted by application
    code; app.activity.record() is the ONLY real writer (the story's own
    rule: "Producers must not write feed rows directly from endpoint
    handlers"). `event_metadata` maps the real `metadata` DB column --
    `metadata` itself is a reserved attribute name on SQLAlchemy's own
    declarative Base (Base.metadata), so the Python-side name has to
    differ from the column name here."""

    __tablename__ = "group_activity"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
