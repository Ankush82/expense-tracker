"""Story 2.1 acceptance criteria tests.

Runs against a real Postgres (expense_db_story8 -- a dedicated database
for this branch).

The story's own acceptance criteria:
  - Creator is automatically owner and appears in the member list.
  - A member (non-admin) receives 403 on PATCH and DELETE.
  - Deleting a group with 40 expenses leaves all 40 in personal history.
  - Group list screen shows an empty state with a "Create a group" CTA.
    (Frontend concern -- not testable here; GET /groups returning an
    empty list for a user in no groups is the backend half, tested
    below.)
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("STORY_2_1_POSTGRES_DB", "expense_db_story8")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-2-1")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_2_1_env(monkeypatch):
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


def _add_member(db_session, *, group_id, user_id, role, removed: bool = False):
    from app.models.group_member import GroupMember

    membership = GroupMember(
        group_id=group_id, user_id=user_id, role=role, removed_at=datetime.now(UTC) if removed else None
    )
    db_session.add(membership)
    db_session.flush()
    return membership


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_creator_is_automatically_owner_and_appears_in_member_list(client, db_session):
    user = _make_user(db_session, suffix="create-a")
    db_session.commit()

    response = client.post("/groups", json={"name": "Flatmates"}, headers=_bearer(user.id))
    assert response.status_code == 201
    body = response.json()
    assert body["caller_role"] == "owner"
    assert body["member_count"] == 1

    detail = client.get(f"/groups/{body['id']}", headers=_bearer(user.id)).json()
    assert len(detail["members"]) == 1
    assert detail["members"][0]["user_id"] == str(user.id)
    assert detail["members"][0]["role"] == "owner"


def test_default_currency_defaults_to_inr(client, db_session):
    user = _make_user(db_session, suffix="create-default-currency")
    db_session.commit()
    response = client.post("/groups", json={"name": "No Currency Given"}, headers=_bearer(user.id))
    assert response.json()["default_currency"] == "INR"


def test_duplicate_name_case_insensitive_for_same_creator_is_rejected(client, db_session):
    user = _make_user(db_session, suffix="dup-a")
    db_session.commit()

    first = client.post("/groups", json={"name": "Roomies"}, headers=_bearer(user.id))
    assert first.status_code == 201
    second = client.post("/groups", json={"name": "ROOMIES"}, headers=_bearer(user.id))
    assert second.status_code == 422
    assert second.json()["detail"][0]["loc"] == ["body", "name"]


def test_same_name_allowed_for_a_different_creator(client, db_session):
    user_a = _make_user(db_session, suffix="dup-b1")
    user_b = _make_user(db_session, suffix="dup-b2")
    db_session.commit()

    first = client.post("/groups", json={"name": "Shared Name"}, headers=_bearer(user_a.id))
    second = client.post("/groups", json={"name": "Shared Name"}, headers=_bearer(user_b.id))
    assert first.status_code == 201
    assert second.status_code == 201


def test_name_too_short_is_422(client, db_session):
    user = _make_user(db_session, suffix="name-short")
    db_session.commit()
    response = client.post("/groups", json={"name": "A"}, headers=_bearer(user.id))
    assert response.status_code == 422


def test_max_20_groups_per_user(client, db_session):
    user = _make_user(db_session, suffix="max-20")
    db_session.commit()

    for i in range(20):
        response = client.post("/groups", json={"name": f"Group {i}"}, headers=_bearer(user.id))
        assert response.status_code == 201, response.json()

    response = client.post("/groups", json={"name": "One too many"}, headers=_bearer(user.id))
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "name"]


# ---------------------------------------------------------------------------
# List / view
# ---------------------------------------------------------------------------


def test_list_returns_empty_for_a_user_in_no_groups(client, db_session):
    user = _make_user(db_session, suffix="empty-list")
    db_session.commit()
    response = client.get("/groups", headers=_bearer(user.id))
    assert response.status_code == 200
    assert response.json() == []


def test_list_shows_only_groups_caller_is_an_active_member_of(client, db_session):
    from app.models.group_member import GroupRole

    user = _make_user(db_session, suffix="list-active")
    stranger = _make_user(db_session, suffix="list-stranger")
    db_session.commit()

    mine = client.post("/groups", json={"name": "Mine"}, headers=_bearer(user.id)).json()
    client.post("/groups", json={"name": "Theirs"}, headers=_bearer(stranger.id))

    response = client.get("/groups", headers=_bearer(user.id))
    ids = {g["id"] for g in response.json()}
    assert mine["id"] in ids
    assert len(response.json()) == 1


def test_a_non_member_gets_404_on_get_detail(client, db_session):
    owner = _make_user(db_session, suffix="detail-owner")
    stranger = _make_user(db_session, suffix="detail-stranger")
    db_session.commit()
    group = client.post("/groups", json={"name": "Private"}, headers=_bearer(owner.id)).json()

    response = client.get(f"/groups/{group['id']}", headers=_bearer(stranger.id))
    assert response.status_code == 403  # require_group_role's own 403, not 404 -- Story 0.4's existing contract


# ---------------------------------------------------------------------------
# Update -- owner/admin only
# ---------------------------------------------------------------------------


def test_member_non_admin_gets_403_on_patch(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="patch-owner")
    db_session.commit()
    group = client.post("/groups", json={"name": "Patch Test"}, headers=_bearer(owner.id)).json()
    member = _make_user(db_session, suffix="patch-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.patch(f"/groups/{group['id']}", json={"name": "Renamed"}, headers=_bearer(member.id))
    assert response.status_code == 403


def test_admin_can_rename_the_group(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="admin-rename-owner")
    db_session.commit()
    group = client.post("/groups", json={"name": "Rename Me"}, headers=_bearer(owner.id)).json()
    admin = _make_user(db_session, suffix="admin-rename-admin")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=admin.id, role=GroupRole.ADMIN)
    db_session.commit()

    response = client.patch(f"/groups/{group['id']}", json={"name": "Renamed By Admin"}, headers=_bearer(admin.id))
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed By Admin"


def test_changing_default_currency_does_not_touch_existing_expenses(client, db_session):
    from app.models.expense import Expense, ExpenseSource

    owner = _make_user(db_session, suffix="currency-owner")
    db_session.commit()
    group = client.post("/groups", json={"name": "Currency Test", "default_currency": "INR"}, headers=_bearer(owner.id)).json()

    expense = Expense(
        user_id=owner.id, group_id=uuid.UUID(group["id"]), amount_minor=500, currency="INR",
        merchant_raw="pre-existing", occurred_at=datetime.now(UTC), source=ExpenseSource.MANUAL, status="confirmed",
    )
    db_session.add(expense)
    db_session.commit()
    expense_id = expense.id

    client.patch(f"/groups/{group['id']}", json={"default_currency": "USD"}, headers=_bearer(owner.id))

    row = db_session.get(Expense, expense_id)
    assert row.currency == "INR"  # untouched by the group's own currency change


# ---------------------------------------------------------------------------
# Delete -- owner only, unlink not cascade
# ---------------------------------------------------------------------------


def test_member_non_admin_gets_403_on_delete(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="del-403-owner")
    db_session.commit()
    group = client.post("/groups", json={"name": "Delete Test"}, headers=_bearer(owner.id)).json()
    member = _make_user(db_session, suffix="del-403-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.delete(f"/groups/{group['id']}", headers=_bearer(member.id))
    assert response.status_code == 403


def test_admin_non_owner_also_gets_403_on_delete(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="del-admin-403-owner")
    db_session.commit()
    group = client.post("/groups", json={"name": "Owner Only Delete"}, headers=_bearer(owner.id)).json()
    admin = _make_user(db_session, suffix="del-admin-403-admin")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=admin.id, role=GroupRole.ADMIN)
    db_session.commit()

    response = client.delete(f"/groups/{group['id']}", headers=_bearer(admin.id))
    assert response.status_code == 403


def test_deleting_a_group_with_40_expenses_leaves_all_40_in_personal_history(client, db_session):
    """The story's own AC, verbatim."""
    from app.models.expense import Expense, ExpenseSource

    owner = _make_user(db_session, suffix="del-40-owner")
    db_session.commit()
    group = client.post("/groups", json={"name": "Forty Expenses"}, headers=_bearer(owner.id)).json()
    group_id = uuid.UUID(group["id"])

    expense_ids = []
    for i in range(40):
        expense = Expense(
            user_id=owner.id, group_id=group_id, amount_minor=100 + i, currency="INR",
            merchant_raw=f"m-{i}", occurred_at=datetime.now(UTC), source=ExpenseSource.MANUAL, status="confirmed",
        )
        db_session.add(expense)
        db_session.flush()
        expense_ids.append(expense.id)
    db_session.commit()

    response = client.delete(f"/groups/{group['id']}", headers=_bearer(owner.id))
    assert response.status_code == 204

    remaining = db_session.execute(
        select(Expense).where(Expense.id.in_(expense_ids), Expense.deleted_at.is_(None))
    ).scalars().all()
    assert len(remaining) == 40  # every expense survives, none soft-deleted
    assert all(e.user_id == owner.id for e in remaining)  # still in personal history
    assert all(e.group_id is None for e in remaining)  # unlinked, not cascade-deleted


def test_deleted_group_itself_is_soft_deleted_and_disappears_from_list(client, db_session):
    owner = _make_user(db_session, suffix="del-group-soft")
    db_session.commit()
    group = client.post("/groups", json={"name": "Soft Delete Me"}, headers=_bearer(owner.id)).json()

    client.delete(f"/groups/{group['id']}", headers=_bearer(owner.id))

    from app.models.group import Group

    row = db_session.get(Group, uuid.UUID(group["id"]))
    assert row.deleted_at is not None

    list_response = client.get("/groups", headers=_bearer(owner.id))
    assert group["id"] not in {g["id"] for g in list_response.json()}
