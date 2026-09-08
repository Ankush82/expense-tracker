import enum
import uuid
from datetime import datetime

from sqlalchemy import CHAR, TIMESTAMP, BigInteger, Enum, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExpenseSource(str, enum.Enum):
    MANUAL = "manual"
    EMAIL = "email"
    CSV = "csv"
    RECURRING = "recurring"


class ExpenseStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"


class Expense(Base):
    """Maps the real `expenses` table (Story 0.2). `group_id` is
    nullable: a personal expense has none. `deleted_at` is a soft
    delete -- Story 0.4's `visibility_filter`/ownership helpers, and
    every future expense query, must filter `deleted_at IS NULL`
    themselves; nothing at the DB layer hides a soft-deleted row."""

    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="RESTRICT"), nullable=True
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    merchant_raw: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[ExpenseSource] = mapped_column(
        Enum(
            "manual", "email", "csv", "recurring",
            name="expense_source",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ExpenseStatus] = mapped_column(
        Enum(
            "pending_review", "confirmed",
            name="expense_status",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
