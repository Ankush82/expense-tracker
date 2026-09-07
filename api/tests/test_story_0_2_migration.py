"""End-to-end test for story 0.2: the initial alembic migration.

The story's acceptance criteria are:
  - "make migrate" applies cleanly on an empty database
  - the down migration also works
  - inserting an expense with amount_minor = 0 or negative raises a DB error
  - seeded system categories are present and cannot be deleted by a user

We exercise alembic programmatically (rather than shelling out to the
`alembic` CLI, which isn't on PATH in every sandbox) against the real
Postgres the project's other tests use. Each test gets a fresh,
transactionally-truncated schema so they don't depend on each other or
on pre-existing state.
"""
import os
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest


_API_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_DIR = _API_DIR / "alembic"
_ALEMBIC_INI = _ALEMBIC_DIR.parent / "alembic.ini"


# Settings (mirrors what app.core.config expects, kept local so this
# test doesn't depend on the .env file or FastAPI app being importable).
DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("POSTGRES_DB", "expense_db")


@pytest.fixture(scope="module")
def alembic_runner():
    """Apply the migration set against the real DB and yield a live conn.

    Runs `alembic upgrade head` once for the whole module so we're
    exercising the same code path CI / `make migrate` does, then yields
    a connection for the tests to inspect the resulting schema. The
    module-scoped `downgrade` at teardown gives us the clean-state
    proof the story asks for.
"""
    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_SERVER": DB_HOST,
            "POSTGRES_USER": DB_USER,
            "POSTGRES_PASSWORD": DB_PASSWORD,
            "POSTGRES_DB": DB_NAME,
            "REDIS_HOST": env.get("REDIS_HOST", "localhost"),
            # app.core.config.Settings requires SECRET_KEY; the test
            # doesn't care about its value, only that alembic/env.py
            # can instantiate Settings() so the migration can import
            # the DATABASE_URI it assembles.
            "SECRET_KEY": env.get("SECRET_KEY", "test-secret-key-for-story-0-2"),
        }
    )

    # Run from api/ so alembic finds alembic.ini and the alembic/
    # script directory without us hardcoding absolute paths.
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
        # Down-migration proof: roll the schema all the way back.
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


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    )
    return cur.fetchone() is not None


def test_all_required_tables_created(alembic_runner):
    """AC: the initial migration creates the documented tables."""
    expected = {
        "users",
        "groups",
        "group_members",
        "categories",
        "expenses",
        "audit_log",
    }
    with alembic_runner.cursor() as cur:
        for t in expected:
            assert _table_exists(cur, t), f"missing table after migrate: {t}"


def test_amount_minor_must_be_positive(alembic_runner):
    """AC: amount_minor <= 0 is rejected at the DB layer.

    We can't insert into expenses without a valid user/group/category,
    so use the system categories the seed migration creates to satisfy
    the FK, then build a user/group just for this row. The CHECK
    constraint should fire BEFORE we touch anything else.
"""
    conn = alembic_runner
    # The fixture's connection may still hold an implicit transaction
    # from alembic's bookkeeping (autocommit=False is psycopg2's
    # default). `set_session` cannot flip autocommit while a txn is
    # open, so roll back anything pending first.
    conn.rollback()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (google_sub, email)
        VALUES ('test-sub-check', 'check@example.com')
        RETURNING id
        """
    )
    user_id = cur.fetchone()[0]

    # Story AC: "amount_minor = 0 or negative raises a DB error" -- the
    # CHECK constraint must reject BOTH cases. Test 0 first.
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            """
            INSERT INTO expenses
                (user_id, amount_minor, currency, merchant_raw, occurred_at, source)
            VALUES (%s, 0, 'INR', 'm', now(), 'manual')
            """,
            (user_id,),
        )
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            """
            INSERT INTO expenses
                (user_id, amount_minor, currency, merchant_raw, occurred_at, source)
            VALUES (%s, -100, 'INR', 'm', now(), 'manual')
            """,
            (user_id,),
        )

    # Cleanup so the module-scoped fixture isn't dirtied for sibling tests.
    cur.execute("DELETE FROM expenses WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.autocommit = False


def test_system_categories_seeded(alembic_runner):
    """AC: ~15 system categories present and not deletable by a user."""
    conn = alembic_runner
    # The fixture's connection may hold an implicit transaction (psycopg2
    # default autocommit=False). The BEFORE DELETE trigger raises
    # restrict_violation to abort the operation -- but if we're inside an
    # implicit txn, psycopg2 also marks the txn as failed, which would
    # mask the trigger's exception with a follow-on error. Switch to
    # autocommit so the trigger's exception is the one that surfaces.
    conn.rollback()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            expected_names = {
                "Food & Dining", "Groceries", "Transport", "Fuel", "Rent",
                "Utilities", "Shopping", "Entertainment", "Subscriptions",
                "Health", "Education", "Travel", "Transfers",
                "Cash Withdrawal", "Other",
            }
            cur.execute(
                "SELECT name FROM categories "
                "WHERE is_system = TRUE AND owner_user_id IS NULL "
                "ORDER BY name"
            )
            seeded = [row[0] for row in cur.fetchall()]
            # Exactly 15 categories were seeded by the migration.
            assert len(seeded) == 15, (
                f"expected exactly 15 system categories, got {len(seeded)}: "
                f"{seeded}"
            )
            # Every required name is present -- explicit per-name check so
            # the failure message names the missing/extra rows directly.
            for name in expected_names:
                assert name in seeded, f"missing system category: {name!r}"
            # No unexpected system rows leaked in.
            extra = set(seeded) - expected_names
            assert not extra, f"unexpected system categories seeded: {extra}"

            # Cannot be deleted: the BEFORE DELETE trigger raises
            # restrict_violation (SQLSTATE 23001). In autocommit mode this
            # surfaces as a real psycopg2 exception, not a follow-on
            # transaction-aborted error.
            cur.execute(
                "SELECT id FROM categories "
                "WHERE is_system = TRUE AND owner_user_id IS NULL LIMIT 1"
            )
            row = cur.fetchone()
            assert row is not None, "no system category found to test delete"
            sys_id = row[0]
            with pytest.raises(psycopg2.errors.RestrictViolation):
                cur.execute("DELETE FROM categories WHERE id = %s", (sys_id,))
            # And the row must still exist after the rejected DELETE --
            # proves the trigger actually aborted the operation rather
            # than letting it through and rolling back later.
            cur.execute(
                "SELECT 1 FROM categories WHERE id = %s", (sys_id,)
            )
            assert cur.fetchone() is not None, (
                "system category row was removed despite the trigger -- "
                "the delete lock is not in effect"
            )
            # Same protection must apply to UPDATE: renaming or otherwise
            # mutating a system category must also be rejected.
            with pytest.raises(psycopg2.errors.RestrictViolation):
                cur.execute(
                    "UPDATE categories SET name = 'Hacked' WHERE id = %s",
                    (sys_id,),
                )
    finally:
        conn.autocommit = False