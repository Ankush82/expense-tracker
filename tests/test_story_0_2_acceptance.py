"""Story 0.2 acceptance criteria tests.

The story's full acceptance criteria are:
  - "make migrate" applies cleanly on an empty database and the down migration
  - Inserting an expense with amount_minor = 0 or negative raises a DB error.
  - Seeded system categories are present and cannot be deleted by a user.

Plus the explicit REQUIREMENTS section:
  - Amounts are bigint minor units. A CHECK constraint rejects amount_minor <= 0.
  - Every foreign key has an explicit ON DELETE behaviour (RESTRICT for anything
    that would orphan money records).
  - Indexes: expenses(user_id, occurred_at desc),
    expenses(group_id, occurred_at desc), expenses(merchant_normalized),
    group_members(user_id).
  - Seed migration inserts ~15 system categories (Food & Dining, Groceries,
    Transport, Fuel, Rent, Utilities, Shopping, Entertainment, Subscriptions,
    Health, Education, Travel, Transfers, Cash Withdrawal, Other) with
    is_system=true and owner_user_id=null.

These tests run AGAINST the same real Postgres the existing
api/tests/test_story_0_2_migration.py tests use, by shelling out to
`alembic upgrade head` / `downgrade base` with the same env settings.
"""
import os
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest


# api/ directory is a sibling of the project-root tests/ directory.
_API_DIR = Path(__file__).resolve().parent.parent / "api"
_ALEMBIC_INI = _API_DIR / "alembic.ini"


DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("POSTGRES_DB", "expense_db")


@pytest.fixture(scope="module")
def alembic_runner():
    """Apply the migration against the real DB and yield a live conn.

    Runs `alembic upgrade head` once for the whole module so we're
    exercising the same code path CI / `make migrate` does, then yields
    a connection for the tests to inspect the resulting schema. Module
    teardown rolls the schema all the way back -- the second half of
    the story's first acceptance criterion.
    """
    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_SERVER": DB_HOST,
            "POSTGRES_USER": DB_USER,
            "POSTGRES_PASSWORD": DB_PASSWORD,
            "POSTGRES_DB": DB_NAME,
            "REDIS_HOST": env.get("REDIS_HOST", "localhost"),
            "SECRET_KEY": env.get("SECRET_KEY", "test-secret-key-for-story-0-2"),
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "alembic upgrade head failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    conn = psycopg2.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME,
    )
    try:
        yield conn
    finally:
        conn.close()
        down = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "base"],
            cwd=_API_DIR,
            env=env,
            capture_output=True,
            text=True,
        )
        if down.returncode != 0:
            pytest.fail(
                "alembic downgrade base failed:\n"
                f"STDOUT:\n{down.stdout}\nSTDERR:\n{down.stderr}"
            )


def test_fk_on_delete_restrict_protects_money_records(alembic_runner):
    """REQUIREMENT: every FK has an explicit ON DELETE; RESTRICT is
    used for any FK that would orphan money records.

    Concretely: deleting a user that owns expenses or audit_log rows
    must be REJECTED, AND deleting a group that owns expenses must be
    REJECTED. If the FK were CASCADE or SET NULL, money rows would be
    silently orphaned -- that is exactly what the requirement forbids.
    """
    conn = alembic_runner
    conn.rollback()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (google_sub, email) "
                "VALUES ('fk-test-sub', 'fk@test.example') RETURNING id"
            )
            user_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO groups (name, created_by) "
                "VALUES ('fk-test-group', %s) RETURNING id",
                (user_id,),
            )
            group_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO expenses
                    (user_id, group_id, amount_minor, currency,
                     merchant_raw, occurred_at, source)
                VALUES (%s, %s, 100, 'INR', 'm', now(), 'manual')
                """,
                (user_id, group_id),
            )
            cur.execute(
                "INSERT INTO audit_log "
                "(actor_user_id, entity_type, entity_id, action) "
                "VALUES (%s, 'expense', 'x', 'create')",
                (user_id,),
            )

            # Deleting the user must fail -- expenses.user_id and
            # audit_log.actor_user_id are FKs and must use RESTRICT.
            with pytest.raises(psycopg2.errors.ForeignKeyViolation):
                cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cur.execute("SELECT 1 FROM users WHERE id = %s", (user_id,))
            assert cur.fetchone() is not None, (
                "user was deleted despite RESTRICT FK from expenses/"
                "audit_log -- money-orphaning DELETE was allowed"
            )

            # Deleting the group must also fail (RESTRICT on
            # expenses.group_id).
            with pytest.raises(psycopg2.errors.ForeignKeyViolation):
                cur.execute("DELETE FROM groups WHERE id = %s", (group_id,))

            # cleanup so sibling tests in this module aren't affected
            cur.execute("DELETE FROM audit_log WHERE actor_user_id = %s",
                        (user_id,))
            cur.execute("DELETE FROM expenses WHERE user_id = %s",
                        (user_id,))
            cur.execute("DELETE FROM groups WHERE id = %s", (group_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    finally:
        conn.autocommit = False


def test_required_indexes_exist_with_desc_occurred_at(alembic_runner):
    """REQUIREMENT: indexes expenses(user_id, occurred_at desc),
    expenses(group_id, occurred_at desc), expenses(merchant_normalized),
    group_members(user_id) must exist after upgrade.

    We read pg_indexes directly so this test verifies the named
    indexes actually got created with the right columns and DESC
    ordering -- not just that some unrelated index happens to exist.
    """
    conn = alembic_runner
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public'"
        )
        rows = cur.fetchall()
        indexes = {name: definition for (name, definition) in rows}

        def _has(column_expr: str, table: str) -> bool:
            needle = f" ON {table} ".lower()
            for definition in indexes.values():
                low = definition.lower()
                if needle in low and column_expr.lower() in low:
                    return True
            return False

        # The (user_id, occurred_at DESC) form -- occurred_at must be
        # DESC-ordered, not default ASC, per the story's spec.
        assert _has("(user_id, occurred_at DESC)", "expenses"), (
            "missing index expenses(user_id, occurred_at DESC); "
            f"found indexes: {list(indexes.keys())}"
        )
        assert _has("(group_id, occurred_at DESC)", "expenses"), (
            "missing index expenses(group_id, occurred_at DESC); "
            f"found indexes: {list(indexes.keys())}"
        )
        assert _has("(merchant_normalized)", "expenses"), (
            "missing index expenses(merchant_normalized); "
            f"found indexes: {list(indexes.keys())}"
        )
        assert _has("(user_id)", "group_members"), (
            "missing index group_members(user_id); "
            f"found indexes: {list(indexes.keys())}"
        )
    finally:
        cur.close()


def test_users_email_is_citext_and_rejects_case_duplicates(alembic_runner):
    """REQUIREMENT: users.email is citext (case-insensitive) and unique.

    The story spec calls out `email citext unique` -- a typo like
    'Foo@x.com' vs 'foo@x.com' must be rejected by the unique
    constraint, and the column type must be the citext extension (not
    plain text, otherwise case-only duplicates slip through and the
    whole point of the column type is lost).
    """
    conn = alembic_runner
    conn.rollback()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'email'
                """
            )
            row = cur.fetchone()
            assert row is not None, "users.email column not found"
            assert row[0] == "citext", (
                f"users.email must be citext (case-insensitive), "
                f"got data_type={row[0]!r}"
            )
            cur.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'citext'"
            )
            assert cur.fetchone() is not None, (
                "citext extension is not installed; users.email cannot "
                "be case-insensitive without it"
            )
            # Case-only duplicates must be rejected.
            cur.execute(
                "INSERT INTO users (google_sub, email) "
                "VALUES ('citex-a', 'Caser@x.example')"
            )
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO users (google_sub, email) "
                    "VALUES ('citex-b', 'caser@x.example')"
                )
            cur.execute("DELETE FROM users WHERE google_sub = 'citex-a'")
    finally:
        conn.autocommit = False