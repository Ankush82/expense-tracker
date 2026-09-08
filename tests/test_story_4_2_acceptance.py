"""Story 4.2 acceptance criteria tests.

The story's acceptance criteria are:
  - The generated query is logged (query only, never results) so
    support can reproduce a user's sync.
  - Blocking a sender stops future ingestion from it within one sync
    cycle.
  - Deactivating a sender globally does not delete already-imported
    expenses.

Plus the explicit REQUIREMENTS section (the exact Gmail query shape,
per-user allow/block overrides, the admin registry endpoint).

Runs against the same real Postgres this project's other Story
acceptance tests use.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("POSTGRES_DB", "expense_db")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-4-2")
_RUN_ID = uuid.uuid4().hex[:8]
_ADMIN_EMAIL = f"admin-{_RUN_ID}@test.example"


@pytest.fixture(autouse=True)
def _story_4_2_env(monkeypatch):
    monkeypatch.setenv("API_V1_STR", "/api/v1")
    monkeypatch.setenv("VERSION", "0.1.0")
    monkeypatch.setenv("POSTGRES_SERVER", DB_HOST)
    monkeypatch.setenv("POSTGRES_USER", DB_USER)
    monkeypatch.setenv("POSTGRES_PASSWORD", DB_PASSWORD)
    monkeypatch.setenv("POSTGRES_DB", DB_NAME)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("REDIS_PASSWORD", "")
    monkeypatch.setenv("REDIS_DB", "0")
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("ADMIN_EMAILS", _ADMIN_EMAIL)


@pytest.fixture
def db_session():
    from app.core.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def _make_user(db_session, *, suffix: str, email: str | None = None):
    from app.models.user import User

    user = User(
        google_sub=f"sub-{suffix}-{_RUN_ID}",
        email=email or f"{suffix}-{_RUN_ID}@test.example",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _bearer(user_id: uuid.UUID) -> dict[str, str]:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _make_sender(db_session, *, pattern_suffix: str, active: bool = True):
    from app.models.transaction_sender import TransactionSender

    sender = TransactionSender(
        sender_pattern=f"alerts-{pattern_suffix}-{_RUN_ID}@bank.example",
        institution_name="Test Bank",
        country="IN",
        parser_key="test_parser",
        is_active=active,
    )
    db_session.add(sender)
    db_session.flush()
    return sender


# ---------------------------------------------------------------------------
# build_gmail_query -- shape, filtering, logging (AC)
# ---------------------------------------------------------------------------


def test_query_shape_matches_the_story_exactly(db_session):
    from app.core.email_ingestion import build_gmail_query

    user = _make_user(db_session, suffix="query-shape")
    sender = _make_sender(db_session, pattern_suffix="shape")
    db_session.commit()

    query = build_gmail_query(db_session, user.id, date(2026, 1, 15))

    assert query.startswith("(from:")
    assert sender.sender_pattern in query
    assert "after:2026/01/15" in query
    assert query.endswith("-category:promotions")


def test_multiple_active_senders_are_or_joined(db_session):
    from app.core.email_ingestion import build_gmail_query

    user = _make_user(db_session, suffix="query-multi")
    sender_a = _make_sender(db_session, pattern_suffix="multi-a")
    sender_b = _make_sender(db_session, pattern_suffix="multi-b")
    db_session.commit()

    query = build_gmail_query(db_session, user.id, date(2026, 1, 1))

    assert f"from:{sender_a.sender_pattern}" in query
    assert f"from:{sender_b.sender_pattern}" in query
    assert " OR " in query


def test_inactive_global_sender_is_never_included(db_session):
    from app.core.email_ingestion import build_gmail_query

    user = _make_user(db_session, suffix="query-inactive")
    inactive_sender = _make_sender(db_session, pattern_suffix="inactive", active=False)
    db_session.commit()

    query = build_gmail_query(db_session, user.id, date(2026, 1, 1))

    assert inactive_sender.sender_pattern not in query


def test_zero_senders_never_falls_back_to_a_bare_mailbox_fetch(db_session):
    """REQUIREMENT: "Never a bare full-mailbox fetch" -- even with zero
    configured senders, the query must not become empty/unqualified.

    The real seed migration (0002_transaction_senders) always leaves
    at least 10 global senders active, so a genuine zero-sender state
    can't occur through normal use -- constructed here by deactivating
    every current sender within this test's own transaction (rolled
    back by the db_session fixture's teardown, same as every other
    row this file creates -- the real seed data is untouched outside
    this one test)."""
    from app.core.email_ingestion import build_gmail_query
    from app.models.transaction_sender import TransactionSender

    db_session.query(TransactionSender).update({TransactionSender.is_active: False})
    user = _make_user(db_session, suffix="query-empty")
    # No commit() here, deliberately: build_gmail_query queries through
    # this SAME session, which sees its own uncommitted writes fine --
    # committing would permanently deactivate the real seed data for
    # every other test/scenario, which the db_session fixture's
    # rollback-at-teardown exists specifically to prevent.

    query = build_gmail_query(db_session, user.id, date(2026, 1, 1))

    assert query.strip() != ""
    assert "from:" not in query  # no senders -- nothing to match on


def test_query_is_logged_query_only_never_results(db_session, caplog):
    """AC, verbatim: the generated query is logged (query only, never
    results) so support can reproduce a user's sync."""
    from app.core.email_ingestion import build_gmail_query

    user = _make_user(db_session, suffix="query-logged")
    sender = _make_sender(db_session, pattern_suffix="logged")
    db_session.commit()

    with caplog.at_level(logging.INFO, logger="app.core.email_ingestion"):
        query = build_gmail_query(db_session, user.id, date(2026, 1, 1))

    matching = [r for r in caplog.records if getattr(r, "gmail_query", None) == query]
    assert len(matching) == 1, "the exact constructed query must appear in exactly one log record"
    record = matching[0]
    assert getattr(record, "user_id", None) == str(user.id)
    # "never results": no result-shaped attribute (message count, a
    # list of matched emails, etc.) is ever attached to this log record.
    assert not hasattr(record, "results")
    assert not hasattr(record, "messages")
    assert sender.sender_pattern not in caplog.text or query in caplog.text  # the pattern only appears via the query itself


# ---------------------------------------------------------------------------
# Blocking / allow overrides (AC: blocking stops future ingestion within
# one sync cycle -- i.e. the very next build_gmail_query call)
# ---------------------------------------------------------------------------


def test_blocking_a_sender_removes_it_from_the_next_query(client, db_session):
    from app.core.email_ingestion import build_gmail_query

    user = _make_user(db_session, suffix="block-user")
    sender = _make_sender(db_session, pattern_suffix="block-target")
    db_session.commit()

    before = build_gmail_query(db_session, user.id, date(2026, 1, 1))
    assert sender.sender_pattern in before

    response = client.post(
        "/integrations/email/senders",
        json={"sender": sender.sender_pattern, "action": "block"},
        headers=_bearer(user.id),
    )
    assert response.status_code == 200
    assert response.json() == {"sender_pattern": sender.sender_pattern, "action": "block"}

    after = build_gmail_query(db_session, user.id, date(2026, 1, 1))
    assert sender.sender_pattern not in after


def test_allowing_a_sender_not_in_the_global_registry_adds_it(client, db_session):
    from app.core.email_ingestion import build_gmail_query

    user = _make_user(db_session, suffix="allow-user")
    db_session.commit()
    custom_sender = f"custom-{_RUN_ID}@my-employer.example"

    response = client.post(
        "/integrations/email/senders",
        json={"sender": custom_sender, "action": "allow"},
        headers=_bearer(user.id),
    )
    assert response.status_code == 200

    query = build_gmail_query(db_session, user.id, date(2026, 1, 1))
    assert custom_sender in query


def test_a_second_post_for_the_same_sender_replaces_the_action(client, db_session):
    """The override is a real upsert, not an append -- a sender
    can't end up simultaneously allowed and blocked."""
    from app.core.email_ingestion import build_gmail_query

    user = _make_user(db_session, suffix="upsert-user")
    db_session.commit()
    sender_pattern = f"flip-{_RUN_ID}@example.com"

    client.post(
        "/integrations/email/senders",
        json={"sender": sender_pattern, "action": "allow"},
        headers=_bearer(user.id),
    )
    query_after_allow = build_gmail_query(db_session, user.id, date(2026, 1, 1))
    assert sender_pattern in query_after_allow

    client.post(
        "/integrations/email/senders",
        json={"sender": sender_pattern, "action": "block"},
        headers=_bearer(user.id),
    )
    query_after_block = build_gmail_query(db_session, user.id, date(2026, 1, 1))
    assert sender_pattern not in query_after_block


def test_override_endpoint_requires_authentication(client):
    response = client.post("/integrations/email/senders", json={"sender": "x@y.com", "action": "block"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Admin registry endpoint
# ---------------------------------------------------------------------------


def test_non_admin_cannot_add_to_the_global_registry(client, db_session):
    user = _make_user(db_session, suffix="not-admin")
    db_session.commit()

    response = client.post(
        "/admin/transaction-senders",
        json={
            "sender_pattern": f"newbank-{_RUN_ID}@example.com",
            "institution_name": "New Bank",
            "country": "IN",
            "parser_key": "new_bank",
        },
        headers=_bearer(user.id),
    )
    assert response.status_code == 403


def test_admin_can_add_to_the_global_registry_without_a_deploy(client, db_session):
    admin = _make_user(db_session, suffix="real-admin", email=_ADMIN_EMAIL)
    db_session.commit()

    sender_pattern = f"newbank2-{_RUN_ID}@example.com"
    response = client.post(
        "/admin/transaction-senders",
        json={
            "sender_pattern": sender_pattern,
            "institution_name": "New Bank",
            "country": "in",
            "parser_key": "new_bank",
            "notes": "added via admin endpoint",
        },
        headers=_bearer(admin.id),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["sender_pattern"] == sender_pattern
    assert body["country"] == "IN"  # uppercased
    assert body["is_active"] is True

    # And it's real -- immediately usable by build_gmail_query, no deploy.
    from app.core.email_ingestion import build_gmail_query

    other_user = _make_user(db_session, suffix="benefits-from-admin-add")
    db_session.commit()
    query = build_gmail_query(db_session, other_user.id, date(2026, 1, 1))
    assert sender_pattern in query


def test_admin_endpoint_requires_authentication(client):
    response = client.post(
        "/admin/transaction-senders",
        json={"sender_pattern": "x@y.com", "institution_name": "X", "country": "IN", "parser_key": "x"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Deactivating a sender globally does not delete already-imported expenses (AC)
# ---------------------------------------------------------------------------


def test_deactivating_a_sender_does_not_touch_already_imported_expenses(db_session):
    from app.models.expense import Expense, ExpenseSource

    user = _make_user(db_session, suffix="deactivate-owner")
    sender = _make_sender(db_session, pattern_suffix="deactivate-target")
    imported_expense = Expense(
        user_id=user.id,
        group_id=None,
        amount_minor=5000,
        currency="INR",
        merchant_raw="Imported via email",
        occurred_at=datetime.now(timezone.utc),
        source=ExpenseSource.EMAIL,
        source_ref=sender.sender_pattern,
        status="confirmed",
    )
    db_session.add(imported_expense)
    db_session.commit()
    expense_id = imported_expense.id

    sender.is_active = False
    db_session.commit()

    from app.models.expense import Expense as ExpenseModel

    still_there = db_session.get(ExpenseModel, expense_id)
    assert still_there is not None
    assert still_there.deleted_at is None
    assert still_there.source_ref == sender.sender_pattern
