import enum
import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Enum, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SenderOverrideAction(str, enum.Enum):
    ALLOW = "allow"
    BLOCK = "block"


class UserSenderOverride(Base):
    """Maps the real `user_sender_overrides` table (Story 4.2):
    POST /integrations/email/senders {sender, action}. A `block` always
    wins over the global registry being active for that sender+user; an
    `allow` only matters for a sender the global registry doesn't (yet)
    carry (see build_gmail_query in app.core.email_ingestion)."""

    __tablename__ = "user_sender_overrides"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    sender_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[SenderOverrideAction] = mapped_column(
        Enum(
            "allow", "block",
            name="sender_override_action",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
