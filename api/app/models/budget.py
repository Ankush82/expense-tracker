import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    BigInteger,
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Integer,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BudgetScope(str, enum.Enum):
    USER = "user"
    GROUP = "group"


class BudgetPeriod(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


class Budget(Base):
    """Maps the real `budgets` table (Story 6.1). Two real DB
    constraints back this model beyond the columns themselves --
    ck_budgets_scope_matches_owner (exactly one of user_id/group_id,
    matching `scope`) and uq_budgets_one_active_per_scope_category_period
    (one active budget per (scope, owner, category, period) at a time) --
    see the migration's own docstring for why both live at the DB layer,
    not only in application code (the story's own explicit AC)."""

    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    scope: Mapped[BudgetScope] = mapped_column(
        Enum(
            "user", "group",
            name="budget_scope",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="RESTRICT"), nullable=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True
    )
    period: Mapped[BudgetPeriod] = mapped_column(
        Enum(
            "daily", "weekly", "monthly", "custom",
            name="budget_period",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    rollover: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    alert_thresholds: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default=text("'{80,100}'")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
