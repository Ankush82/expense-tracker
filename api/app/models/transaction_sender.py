import uuid
from datetime import datetime

from sqlalchemy import CHAR, TIMESTAMP, Boolean, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TransactionSender(Base):
    """Maps the real `transaction_senders` table (Story 4.2) -- the
    curated, global registry the Gmail query in `build_gmail_query()`
    (app.core.email_ingestion) is built from. `is_active = false`
    stops the sender from ever appearing in a future query without
    deleting the row (and without touching any expense already
    imported from it -- the story's own explicit requirement)."""

    __tablename__ = "transaction_senders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    sender_pattern: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    institution_name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    parser_key: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
