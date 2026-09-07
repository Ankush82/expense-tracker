"""initial schema: users, groups, group_members, categories, expenses, audit_log

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-01-01 00:00:00

Story 0.2 baseline. Creates the foundational tables that later epics extend.
- Amounts are bigint minor units with a CHECK rejecting <= 0.
- Every foreign key has an explicit ON DELETE behaviour; RESTRICT is used
  for anything that would orphan money records (expenses, audit_log,
  group ownership, category parent-of-expense).
- Indexes: expenses(user_id, occurred_at desc), expenses(group_id,
  occurred_at desc), expenses(merchant_normalized), group_members(user_id).
- Seed migration inserts ~15 system categories that no user can delete
  (is_system=true, owner_user_id=null, plus an is_system lock that
  rejects DELETE/UPDATE on those rows).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum types ---------------------------------------------------------------
# Defined as Python-level enums so the values stay in one place; the DB
# types are created as native Postgres ENUMs via sa.Enum(..., name=...).
group_role = sa.Enum("owner", "admin", "member", name="group_role")
expense_source = sa.Enum(
    "manual", "email", "csv", "recurring", name="expense_source"
)
expense_status = sa.Enum(
    "pending_review", "confirmed", name="expense_status"
)


def _default_currency_column() -> sa.Column:
    """The CHAR(3) default-currency column used by both users and groups.

    Extracted into a single helper so the column definition (length,
    nullability, server default) lives in exactly one place -- if we ever
    change the default or the length, it's a one-line edit instead of a
    search-and-replace that's easy to get wrong.
    """
    return sa.Column(
        "default_currency",
        sa.CHAR(length=3),
        nullable=False,
        server_default=sa.text("'INR'"),
    )


def _created_at_column() -> sa.Column:
    """The standard `created_at timestamptz default now()` column.

    Used by every table that tracks creation time (users, groups). Kept in
    one helper so the column type/default never drifts between tables.
    """
    return sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _updated_at_column() -> sa.Column:
    """The standard `updated_at timestamptz default now()` column.

    Mirror of ``_created_at_column`` for the updated_at pair on the same
    tables (users, groups). Defined in one place so the type/default
    can't drift between the created_at and updated_at helpers, and so
    any future change to the column shape is a one-line edit.
    """
    return sa.Column(
        "updated_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    # Extensions ----------------------------------------------------------
    # citext = case-insensitive text (used for users.email so we can rely
    # on a unique constraint that matches what the app will see).
    # pgcrypto = gen_random_uuid() for client-generated UUID defaults.
    op.execute('CREATE EXTENSION IF NOT EXISTS "citext"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # Enums ---------------------------------------------------------------
    # NOTE: do NOT pre-create the enum types here. Each sa.Enum() below
    # is declared as a column on a table (group_members.role,
    # expenses.source, expenses.status), so SQLAlchemy emits CREATE TYPE
    # automatically as part of the matching op.create_table(...) call.
    # Pre-creating them here would race with the implicit create and
    # raise DuplicateObject -- checkfirst=True does NOT issue
    # IF NOT EXISTS, it only checks SQLAlchemy's in-memory table state.
    # The symmetric .drop(checkfirst=True) calls remain in downgrade().

    # users ---------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("google_sub", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.dialects.postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column(
            "timezone",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'Asia/Kolkata'"),
        ),
        _default_currency_column(),
        _created_at_column(),
        _updated_at_column(),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # groups --------------------------------------------------------------
    op.create_table(
        "groups",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        _default_currency_column(),
        _created_at_column(),
        _updated_at_column(),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # group_members -------------------------------------------------------
    op.create_table(
        "group_members",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "role",
            group_role,
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Index required by the story: group_members(user_id).
    op.create_index(
        "ix_group_members_user_id",
        "group_members",
        ["user_id"],
    )
    # Unique (group_id, user_id) WHERE removed_at IS NULL -- a removed
    # member can be re-added later, but an active membership is unique.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_group_members_active
        ON group_members (group_id, user_id)
        WHERE removed_at IS NULL
        """
    )

    # categories ----------------------------------------------------------
    op.create_table(
        "categories",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "parent_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Unique (owner_user_id, lower(name)) WHERE deleted_at IS NULL.
    # Two system rows (owner_user_id IS NULL) can't share a lower(name),
    # and a user's own row can't collide with a same-named system row
    # because the system row's owner_user_id IS NULL.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_categories_owner_lower_name
        ON categories (owner_user_id, lower(name))
        WHERE deleted_at IS NULL
        """
    )

    # expenses ------------------------------------------------------------
    op.create_table(
        "expenses",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("merchant_raw", sa.Text(), nullable=False),
        sa.Column("merchant_normalized", sa.Text(), nullable=True),
        sa.Column(
            "category_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "source",
            expense_source,
            nullable=False,
        ),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column(
            "status",
            expense_status,
            nullable=False,
            server_default=sa.text("'confirmed'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Money must be a positive integer in minor units (e.g. paise).
        sa.CheckConstraint(
            "amount_minor > 0", name="ck_expenses_amount_minor_positive"
        ),
    )
    # Indexes required by the story.
    op.create_index(
        "ix_expenses_user_id_occurred_at",
        "expenses",
        [sa.text("user_id"), sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_expenses_group_id_occurred_at",
        "expenses",
        [sa.text("group_id"), sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_expenses_merchant_normalized",
        "expenses",
        ["merchant_normalized"],
    )

    # audit_log -----------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "actor_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("before", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("after", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # System-categories lock ---------------------------------------------
    # The story's third acceptance criterion: "Seeded system categories
    # are present and cannot be deleted by a user." Enforce that at the
    # DB layer so even a stray SQL session can't bypass it.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_system_category_change()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.is_system = TRUE THEN
                RAISE EXCEPTION
                    'system category (id=%) is immutable', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    # The DELETE/UPDATE triggers both fire BEFORE the row is removed/
    # mutated so the operation aborts before any data is lost.
    op.execute(
        """
        CREATE TRIGGER trg_categories_no_delete_system
        BEFORE DELETE ON categories
        FOR EACH ROW EXECUTE FUNCTION reject_system_category_change();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_categories_no_update_system
        BEFORE UPDATE ON categories
        FOR EACH ROW EXECUTE FUNCTION reject_system_category_change();
        """
    )

    # Seed: ~15 system categories ---------------------------------------
    # icon + color left NULL -- the app can render defaults, and a future
    # seed refresh can populate them. Names are case-folded by the unique
    # index, so inserting via lower(name) keeps us safe.
    system_categories = [
        ("Food & Dining",),
        ("Groceries",),
        ("Transport",),
        ("Fuel",),
        ("Rent",),
        ("Utilities",),
        ("Shopping",),
        ("Entertainment",),
        ("Subscriptions",),
        ("Health",),
        ("Education",),
        ("Travel",),
        ("Transfers",),
        ("Cash Withdrawal",),
        ("Other",),
    ]
    for (name,) in system_categories:
        op.execute(
            sa.text(
                """
                INSERT INTO categories
                    (owner_user_id, name, is_system)
                VALUES
                    (NULL, :name, TRUE)
                """
            ).bindparams(name=name)
        )


def downgrade() -> None:
    # Reverse in FK-respecting order: triggers/extensions last, leaf
    # tables first. audit_log -> expenses -> categories -> group_members
    # -> groups -> users -> enums -> triggers -> extensions.
    op.execute("DROP TRIGGER IF EXISTS trg_categories_no_update_system ON categories")
    op.execute("DROP TRIGGER IF EXISTS trg_categories_no_delete_system ON categories")
    op.execute("DROP FUNCTION IF EXISTS reject_system_category_change()")

    op.drop_table("audit_log")

    op.drop_index("ix_expenses_merchant_normalized", table_name="expenses")
    op.drop_index("ix_expenses_group_id_occurred_at", table_name="expenses")
    op.drop_index("ix_expenses_user_id_occurred_at", table_name="expenses")
    op.drop_table("expenses")

    op.execute("DROP INDEX IF EXISTS uq_categories_owner_lower_name")
    op.drop_table("categories")

    op.execute("DROP INDEX IF EXISTS uq_group_members_active")
    op.drop_index("ix_group_members_user_id", table_name="group_members")
    op.drop_table("group_members")

    op.drop_table("groups")

    op.drop_table("users")

    expense_status.drop(op.get_bind(), checkfirst=True)
    expense_source.drop(op.get_bind(), checkfirst=True)
    group_role.drop(op.get_bind(), checkfirst=True)

    # Extensions are intentionally NOT dropped: they may be in use by
    # other databases on this cluster, and dropping them is unsafe. The
    # down migration only undoes what THIS migration added (tables,
    # enums, triggers, function, indexes).