"""Story 6.1 acceptance criteria tests.

Runs against a real Postgres (expense_db_story30 -- a dedicated database
for this branch).

The story's own acceptance criteria:
  - All four period types create and read back correctly.
  - A member attempting to create a group budget gets 403.
  - Deleting a budget stops its alerts immediately. (Alerts themselves
    are Story 6.4's own scope, not built yet -- verified here at this
    story's real scope: deleted_at is set synchronously and the budget
    disappears from every real query that filters it, which is what any
    future alert-checking code would do.)
  - Constraint violation (both user_id and group_id set) is rejected at
    DB level, not only in application code.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("STORY_6_1_POSTGRES_DB", "expense_db_story30")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-6-1")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_6_1_env(monkeypatch):
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


def _make_user(db_session, *, suffix: str):
    from app.models.user import User

    user = User(google_sub=f"sub-{suffix}-{_RUN_ID}", email=f"{suffix}-{_RUN_ID}@test.example")
    db_session.add(user)
    db_session.flush()
    return user


def _bearer(user_id: uuid.UUID) -> dict[str, str]:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _make_group(client, owner, *, name: str) -> dict:
    return client.post("/groups", json={"name": name}, headers=_bearer(owner.id)).json()


def _add_member(db_session, *, group_id, user_id, role):
    from app.models.group_member import GroupMember

    membership = GroupMember(group_id=group_id, user_id=user_id, role=role, removed_at=None)
    db_session.add(membership)
    db_session.flush()
    return membership


def _user_budget_body(**overrides) -> dict:
    body = {"scope": "user", "period": "monthly", "amount_minor": 500000, "currency": "INR"}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# All four period types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
def test_computed_period_types_create_and_read_back(client, db_session, period):
    user = _make_user(db_session, suffix=f"period-{period}")
    db_session.commit()

    response = client.post(
        "/budgets", json=_user_budget_body(period=period, amount_minor=10000), headers=_bearer(user.id)
    )
    assert response.status_code == 201
    body = response.json()
    assert body["period"] == period
    assert body["period_start"] <= body["period_end"]

    fetched = client.get(f"/budgets/{body['id']}", headers=_bearer(user.id)).json()
    assert fetched["id"] == body["id"]
    assert fetched["period_start"] == body["period_start"]
    assert fetched["period_end"] == body["period_end"]


def test_custom_period_requires_explicit_bounds(client, db_session):
    user = _make_user(db_session, suffix="custom-missing")
    db_session.commit()

    response = client.post("/budgets", json=_user_budget_body(period="custom"), headers=_bearer(user.id))
    assert response.status_code == 422


def test_custom_period_creates_and_reads_back_with_explicit_bounds(client, db_session):
    user = _make_user(db_session, suffix="custom-ok")
    db_session.commit()

    response = client.post(
        "/budgets",
        json=_user_budget_body(period="custom", period_start="2026-03-01", period_end="2026-03-15"),
        headers=_bearer(user.id),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["period_start"] == "2026-03-01"
    assert body["period_end"] == "2026-03-15"


def test_weekly_period_starts_on_monday(client, db_session):
    user = _make_user(db_session, suffix="weekly-monday")
    db_session.commit()

    response = client.post("/budgets", json=_user_budget_body(period="weekly"), headers=_bearer(user.id))
    body = response.json()
    start = date.fromisoformat(body["period_start"])
    assert start.weekday() == 0  # Monday


def test_custom_period_end_before_start_is_422(client, db_session):
    user = _make_user(db_session, suffix="custom-backwards")
    db_session.commit()

    response = client.post(
        "/budgets",
        json=_user_budget_body(period="custom", period_start="2026-03-15", period_end="2026-03-01"),
        headers=_bearer(user.id),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Group budgets -- admin+ only
# ---------------------------------------------------------------------------


def test_member_attempting_to_create_a_group_budget_gets_403(client, db_session):
    """The story's own AC, verbatim."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="grp-budget-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Budget Group")
    member = _make_user(db_session, suffix="grp-budget-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.post(
        "/budgets",
        json=_user_budget_body(scope="group", group_id=group["id"]),
        headers=_bearer(member.id),
    )
    assert response.status_code == 403


def test_admin_can_create_a_group_budget(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="grp-budget-admin-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Admin Budget Group")
    admin = _make_user(db_session, suffix="grp-budget-admin")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=admin.id, role=GroupRole.ADMIN)
    db_session.commit()

    response = client.post(
        "/budgets",
        json=_user_budget_body(scope="group", group_id=group["id"]),
        headers=_bearer(admin.id),
    )
    assert response.status_code == 201
    assert response.json()["group_id"] == group["id"]
    assert response.json()["user_id"] is None


def test_group_budget_missing_group_id_is_422(client, db_session):
    user = _make_user(db_session, suffix="grp-missing-id")
    db_session.commit()
    response = client.post("/budgets", json=_user_budget_body(scope="group"), headers=_bearer(user.id))
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Delete -- stops alerts immediately (real scope: deleted_at + disappears)
# ---------------------------------------------------------------------------


def test_deleting_a_budget_sets_deleted_at_and_it_disappears_from_list(client, db_session):
    from app.models.budget import Budget

    user = _make_user(db_session, suffix="del-budget")
    db_session.commit()
    created = client.post("/budgets", json=_user_budget_body(), headers=_bearer(user.id)).json()

    response = client.delete(f"/budgets/{created['id']}", headers=_bearer(user.id))
    assert response.status_code == 204

    row = db_session.get(Budget, uuid.UUID(created["id"]))
    assert row.deleted_at is not None

    list_response = client.get("/budgets", headers=_bearer(user.id))
    assert created["id"] not in {b["id"] for b in list_response.json()}

    get_response = client.get(f"/budgets/{created['id']}", headers=_bearer(user.id))
    assert get_response.status_code == 404


def test_only_owner_can_delete_a_user_budget(client, db_session):
    owner = _make_user(db_session, suffix="del-owner-only")
    stranger = _make_user(db_session, suffix="del-owner-stranger")
    db_session.commit()
    created = client.post("/budgets", json=_user_budget_body(), headers=_bearer(owner.id)).json()

    response = client.delete(f"/budgets/{created['id']}", headers=_bearer(stranger.id))
    assert response.status_code == 404  # not even visible to a stranger


# ---------------------------------------------------------------------------
# DB-level constraints
# ---------------------------------------------------------------------------


def test_both_user_id_and_group_id_set_is_rejected_at_db_level(db_session):
    """The story's own AC, verbatim: "Constraint violation (both user_id
    and group_id set) is rejected at DB level, not only in application
    code." Bypasses the API entirely -- constructs the ORM object
    directly and asserts the real DB CHECK constraint fires."""
    from sqlalchemy.exc import IntegrityError

    from app.models.budget import Budget, BudgetPeriod, BudgetScope
    from app.models.group import Group

    owner = _make_user(db_session, suffix="ck-owner")
    db_session.flush()
    group = Group(name=f"ck-group-{_RUN_ID}", created_by=owner.id)
    db_session.add(group)
    db_session.flush()

    bad_budget = Budget(
        scope=BudgetScope.USER,
        user_id=owner.id,
        group_id=group.id,  # both set -- must be rejected
        period=BudgetPeriod.MONTHLY,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        amount_minor=1000,
        currency="INR",
        created_by=owner.id,
    )
    db_session.add(bad_budget)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_conflicting_active_budget_is_409_with_existing_id(client, db_session):
    """The story's own rule: "A user may have one active budget per
    (scope, category, period) combination; creating a conflicting one
    returns 409 and offers to replace." -- enforced by a real DB partial
    unique index, surfaced here as a real 409 naming the existing row."""
    user = _make_user(db_session, suffix="conflict")
    db_session.commit()

    first = client.post("/budgets", json=_user_budget_body(period="monthly"), headers=_bearer(user.id))
    assert first.status_code == 201

    second = client.post("/budgets", json=_user_budget_body(period="monthly"), headers=_bearer(user.id))
    assert second.status_code == 409
    assert second.json()["detail"]["existing_budget_id"] == first.json()["id"]


def test_different_category_does_not_conflict(client, db_session):
    user = _make_user(db_session, suffix="no-conflict-cat")
    db_session.commit()
    from app.models.category import Category

    category = Category(owner_user_id=user.id, name="Groceries Only", is_system=False)
    db_session.add(category)
    db_session.commit()

    first = client.post("/budgets", json=_user_budget_body(period="monthly"), headers=_bearer(user.id))
    second = client.post(
        "/budgets", json=_user_budget_body(period="monthly", category_id=str(category.id)), headers=_bearer(user.id)
    )
    assert first.status_code == 201
    assert second.status_code == 201


# ---------------------------------------------------------------------------
# Rollover feature flag
# ---------------------------------------------------------------------------


def test_rollover_true_is_rejected_while_flag_is_off(client, db_session):
    user = _make_user(db_session, suffix="rollover-off")
    db_session.commit()
    response = client.post(
        "/budgets", json=_user_budget_body(rollover=True), headers=_bearer(user.id)
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "rollover"]


def test_rollover_false_default_is_accepted(client, db_session):
    user = _make_user(db_session, suffix="rollover-default")
    db_session.commit()
    response = client.post("/budgets", json=_user_budget_body(), headers=_bearer(user.id))
    assert response.status_code == 201
    assert response.json()["rollover"] is False
