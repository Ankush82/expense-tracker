"""transaction_senders registry + per-user sender overrides

Revision ID: 0002_transaction_senders
Revises: 0001_initial_schema
Create Date: 2026-01-02 00:00:00

Story 4.2. Two real tables:

- transaction_senders: the curated, global registry Story 4.2's Gmail
  query is built from. Seeded with a SMALL starter set of well-known
  Indian bank/payment-provider sender patterns -- the story's own text
  explicitly says "final list to be confirmed with me before the seed
  migration is written," so this seed is intentionally a starting
  point, not a final, exhaustive list. Confirm/extend the real
  patterns before relying on this in production; `is_active` lets a
  bad pattern be turned off without a deploy either way.
- user_sender_overrides: per-user allow/block overrides layered on top
  of the global registry (POST /integrations/email/senders). A block
  always wins over the global registry being active; an allow only
  matters for a sender the global registry doesn't (yet) carry.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_transaction_senders"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

override_action = sa.Enum("allow", "block", name="sender_override_action")


def upgrade() -> None:
    op.create_table(
        "transaction_senders",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("sender_pattern", sa.Text(), nullable=False, unique=True),
        sa.Column("institution_name", sa.Text(), nullable=False),
        sa.Column("country", sa.CHAR(length=2), nullable=False),
        sa.Column("parser_key", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "user_sender_overrides",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender_pattern", sa.Text(), nullable=False),
        sa.Column("action", override_action, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # One real override per (user, sender) -- a second POST for the same
    # pair updates the existing row's action rather than creating a
    # duplicate (Story's own endpoint enforces this via upsert).
    op.create_index(
        "uq_user_sender_overrides_user_sender",
        "user_sender_overrides",
        ["user_id", "sender_pattern"],
        unique=True,
    )

    # Seed: STARTING SET ONLY -- see module docstring. sender_pattern is
    # a real Gmail `from:` match value (a bare domain matches any local
    # part at that domain, which is how these transaction-notification
    # senders are commonly structured).
    op.execute(
        """
        INSERT INTO transaction_senders
            (sender_pattern, institution_name, country, parser_key, is_active, notes)
        VALUES
            ('alerts@hdfcbank.net', 'HDFC Bank', 'IN', 'hdfc_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('credit_cards@hdfcbank.net', 'HDFC Bank', 'IN', 'hdfc_cc_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('alerts@icicibank.com', 'ICICI Bank', 'IN', 'icici_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('credit_cards@icicibank.com', 'ICICI Bank', 'IN', 'icici_cc_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('alerts@sbi.co.in', 'State Bank of India', 'IN', 'sbi_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('alerts@axisbank.com', 'Axis Bank', 'IN', 'axis_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('alerts@kotak.com', 'Kotak Mahindra Bank', 'IN', 'kotak_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('alerts@americanexpress.com', 'American Express India', 'IN', 'amex_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('alerts@hsbc.co.in', 'HSBC India', 'IN', 'hsbc_alerts', true, 'starter set -- confirm real pattern before relying on this'),
            ('alerts@npci.org.in', 'UPI (NPCI)', 'IN', 'upi_alerts', true, 'starter set -- confirm real pattern before relying on this')
        """
    )


def downgrade() -> None:
    op.drop_index("uq_user_sender_overrides_user_sender", table_name="user_sender_overrides")
    op.drop_table("user_sender_overrides")
    op.drop_table("transaction_senders")
    override_action.drop(op.get_bind(), checkfirst=True)
