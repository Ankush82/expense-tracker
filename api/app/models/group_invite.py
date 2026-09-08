import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Enum, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.group_member import GroupRole


class GroupInvite(Base):
    """Maps the real `group_invites` table (Story 2.2). `token_hash` is
    the ONLY thing ever persisted for the invite token -- the raw token
    is returned to the caller exactly once, at creation, and never
    stored or logged anywhere (the story's own security requirement:
    "Tokens never appear in server logs..."). `email is None` means a
    link invite; `email is not None` means a single-use email invite
    tied to that address."""

    __tablename__ = "group_invites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    invited_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    role: Mapped[GroupRole] = mapped_column(
        Enum(
            "owner", "admin", "member",
            name="group_role",
            create_type=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    send_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    last_sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))

    @property
    def is_live(self) -> bool:
        """A "pending" invite for the purposes of GET (listing) and the
        re-invite/resend rule: not yet accepted, not revoked, not past
        its own expiry. Real, single source of truth so the router never
        has to repeat this three-way check inline."""
        from datetime import UTC

        now = datetime.now(UTC)
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > now
