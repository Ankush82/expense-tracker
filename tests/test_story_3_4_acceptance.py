"""Story 3.4 acceptance criteria tests.

Runs against a real Postgres (expense_db_story15 -- a dedicated database
for this branch; see this branch's own migration docstrings for why a
dedicated database, not the shared local expense_db, is used).

The story's own acceptance criteria:
  - Deleting a category with 30 expenses moves all 30 and leaves no
    orphans.
  - Attempting to delete a system category returns 403.
  - Category picker groups system and custom categories under clear
    headings. (Frontend concern -- not testable here; GET /categories
    returning `is_system` on every row is what a frontend would group
    by, verified below.)
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
DB_NAME = os.environ.get("STORY_3_4_POSTGRES_DB", "expense_db_story15")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-3-4")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_3_4_env(monkeypatch):
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


def _system_category_id(db_session, *, exclude: set[str] = frozenset()) -> str:
    from app.models.category import Category

    rows = db_session.execute(select(Category).where(Category.is_system.is_(True))).scalars().all()
    for row in rows:
        if str(row.id) not in exclude:
            return str(row.id)
    raise AssertionError("no system category available")


def _make_expense(db_session, *, user_id, category_id, suffix: str):
    from app.models.expense import Expense, ExpenseSource

    expense = Expense(
        user_id=user_id,
        group_id=None,
        amount_minor=100,
        currency="INR",
        merchant_raw=f"m-{suffix}",
        category_id=category_id,
        occurred_at=datetime.now(UTC),
        source=ExpenseSource.MANUAL,
        status="confirmed",
    )
    db_session.add(expense)
    db_session.flush()
    return expense


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_category_succeeds_and_is_owned_by_caller(client, db_session):
    user = _make_user(db_session, suffix="create-a")
    db_session.commit()

    response = client.post("/categories", json={"name": "Pets"}, headers=_bearer(user.id))
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Pets"
    assert body["is_system"] is False
    assert body["owner_user_id"] == str(user.id)


def test_duplicate_name_case_insensitive_within_a_user_is_rejected(client, db_session):
    user = _make_user(db_session, suffix="dup-a")
    db_session.commit()

    first = client.post("/categories", json={"name": "Pets"}, headers=_bearer(user.id))
    assert first.status_code == 201

    second = client.post("/categories", json={"name": "PETS"}, headers=_bearer(user.id))
    assert second.status_code == 422
    assert second.json()["detail"][0]["loc"] == ["body", "name"]


def test_same_name_is_allowed_for_a_different_user(client, db_session):
    user_a = _make_user(db_session, suffix="dup-b1")
    user_b = _make_user(db_session, suffix="dup-b2")
    db_session.commit()

    first = client.post("/categories", json={"name": "Pets"}, headers=_bearer(user_a.id))
    second = client.post("/categories", json={"name": "Pets"}, headers=_bearer(user_b.id))
    assert first.status_code == 201
    assert second.status_code == 201


def test_max_50_custom_categories_per_user(client, db_session):
    user = _make_user(db_session, suffix="max-a")
    db_session.commit()

    for i in range(50):
        response = client.post("/categories", json={"name": f"Cat {i}"}, headers=_bearer(user.id))
        assert response.status_code == 201, response.json()

    response = client.post("/categories", json={"name": "One too many"}, headers=_bearer(user.id))
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "name"]


def test_one_level_of_nesting_only(client, db_session):
    user = _make_user(db_session, suffix="nest-a")
    db_session.commit()

    parent = client.post("/categories", json={"name": "Parent"}, headers=_bearer(user.id)).json()
    child = client.post(
        "/categories", json={"name": "Child", "parent_id": parent["id"]}, headers=_bearer(user.id)
    )
    assert child.status_code == 201

    grandchild = client.post(
        "/categories", json={"name": "Grandchild", "parent_id": child.json()["id"]}, headers=_bearer(user.id)
    )
    assert grandchild.status_code == 422
    assert grandchild.json()["detail"][0]["loc"] == ["body", "parent_id"]


def test_parent_id_must_be_visible_to_caller(client, db_session):
    owner = _make_user(db_session, suffix="parent-owner")
    other = _make_user(db_session, suffix="parent-other")
    db_session.commit()

    owners_category = client.post("/categories", json={"name": "Mine"}, headers=_bearer(owner.id)).json()

    response = client.post(
        "/categories", json={"name": "Not allowed", "parent_id": owners_category["id"]}, headers=_bearer(other.id)
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "parent_id"]


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_returns_system_categories_and_only_callers_own_custom_ones(client, db_session):
    user = _make_user(db_session, suffix="list-a")
    other = _make_user(db_session, suffix="list-b")
    db_session.commit()

    mine = client.post("/categories", json={"name": "Mine-only"}, headers=_bearer(user.id)).json()
    client.post("/categories", json={"name": "Theirs-only"}, headers=_bearer(other.id))

    response = client.get("/categories", headers=_bearer(user.id))
    names = {c["name"] for c in response.json()}
    assert "Mine-only" in names
    assert "Theirs-only" not in names
    assert any(c["is_system"] for c in response.json())  # system categories present
    assert mine["id"] in {c["id"] for c in response.json()}


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_a_system_category_is_403(client, db_session):
    user = _make_user(db_session, suffix="upd-sys")
    db_session.commit()
    category_id = _system_category_id(db_session)

    response = client.patch(f"/categories/{category_id}", json={"name": "Hacked"}, headers=_bearer(user.id))
    assert response.status_code == 403


def test_update_a_custom_category_by_its_owner_succeeds(client, db_session):
    user = _make_user(db_session, suffix="upd-a")
    db_session.commit()
    created = client.post("/categories", json={"name": "Old Name"}, headers=_bearer(user.id)).json()

    response = client.patch(f"/categories/{created['id']}", json={"name": "New Name"}, headers=_bearer(user.id))
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


def test_update_someone_elses_custom_category_is_404_not_403(client, db_session):
    """A stranger's custom category isn't merely off-limits to edit --
    it isn't visible to a non-owner at all (same "404, never 403, don't
    leak existence" rule as Story 3.1's expenses)."""
    owner = _make_user(db_session, suffix="upd-owner")
    stranger = _make_user(db_session, suffix="upd-stranger")
    db_session.commit()
    created = client.post("/categories", json={"name": "Owner's"}, headers=_bearer(owner.id)).json()

    response = client.patch(f"/categories/{created['id']}", json={"name": "Hijacked"}, headers=_bearer(stranger.id))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Delete -- reassignment, no orphans
# ---------------------------------------------------------------------------


def test_delete_system_category_is_403(client, db_session):
    user = _make_user(db_session, suffix="del-sys")
    db_session.commit()
    category_id = _system_category_id(db_session)
    other_category_id = _system_category_id(db_session, exclude={category_id})

    response = client.delete(
        f"/categories/{category_id}", params={"reassign_to": other_category_id}, headers=_bearer(user.id)
    )
    assert response.status_code == 403


def test_delete_without_reassign_to_is_a_422(client, db_session):
    user = _make_user(db_session, suffix="del-noreassign")
    db_session.commit()
    created = client.post("/categories", json={"name": "To Delete"}, headers=_bearer(user.id)).json()

    response = client.delete(f"/categories/{created['id']}", headers=_bearer(user.id))
    assert response.status_code == 422


def test_deleting_a_category_with_30_expenses_moves_all_30_and_leaves_no_orphans(client, db_session):
    from app.models.category import Category
    from app.models.expense import Expense

    user = _make_user(db_session, suffix="del-30")
    db_session.commit()
    source = client.post("/categories", json={"name": "Source"}, headers=_bearer(user.id)).json()
    target = client.post("/categories", json={"name": "Target"}, headers=_bearer(user.id)).json()

    for i in range(30):
        _make_expense(db_session, user_id=user.id, category_id=uuid.UUID(source["id"]), suffix=str(i))
    db_session.commit()

    response = client.delete(
        f"/categories/{source['id']}", params={"reassign_to": target["id"]}, headers=_bearer(user.id)
    )
    assert response.status_code == 200

    remaining_on_source = db_session.execute(
        select(Expense).where(Expense.category_id == uuid.UUID(source["id"]))
    ).scalars().all()
    assert remaining_on_source == []

    moved_to_target = db_session.execute(
        select(Expense).where(Expense.category_id == uuid.UUID(target["id"]))
    ).scalars().all()
    assert len(moved_to_target) == 30

    deleted_category = db_session.get(Category, uuid.UUID(source["id"]))
    assert deleted_category.deleted_at is not None


def test_delete_someone_elses_custom_category_is_404_not_403(client, db_session):
    owner = _make_user(db_session, suffix="del-owner")
    stranger = _make_user(db_session, suffix="del-stranger")
    db_session.commit()
    created = client.post("/categories", json={"name": "Owner's"}, headers=_bearer(owner.id)).json()
    other_target = client.post("/categories", json={"name": "Stranger's"}, headers=_bearer(stranger.id)).json()

    response = client.delete(
        f"/categories/{created['id']}", params={"reassign_to": other_target["id"]}, headers=_bearer(stranger.id)
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Hide / unhide a system category
# ---------------------------------------------------------------------------


def test_hide_a_system_category_marks_it_hidden_for_that_user_only(client, db_session):
    user = _make_user(db_session, suffix="hide-a")
    other = _make_user(db_session, suffix="hide-b")
    db_session.commit()
    category_id = _system_category_id(db_session)

    hide_response = client.post(f"/categories/{category_id}/hide", headers=_bearer(user.id))
    assert hide_response.status_code == 204

    mine = {c["id"]: c["hidden"] for c in client.get("/categories", headers=_bearer(user.id)).json()}
    assert mine[category_id] is True

    theirs = {c["id"]: c["hidden"] for c in client.get("/categories", headers=_bearer(other.id)).json()}
    assert theirs[category_id] is False


def test_hiding_a_category_twice_is_idempotent(client, db_session):
    user = _make_user(db_session, suffix="hide-idem")
    db_session.commit()
    category_id = _system_category_id(db_session)

    first = client.post(f"/categories/{category_id}/hide", headers=_bearer(user.id))
    second = client.post(f"/categories/{category_id}/hide", headers=_bearer(user.id))
    assert first.status_code == 204
    assert second.status_code == 204


def test_hiding_a_custom_category_is_rejected(client, db_session):
    user = _make_user(db_session, suffix="hide-custom")
    db_session.commit()
    created = client.post("/categories", json={"name": "Mine"}, headers=_bearer(user.id)).json()

    response = client.post(f"/categories/{created['id']}/hide", headers=_bearer(user.id))
    assert response.status_code == 422


def test_unhide_makes_it_visible_again(client, db_session):
    user = _make_user(db_session, suffix="unhide-a")
    db_session.commit()
    category_id = _system_category_id(db_session)

    client.post(f"/categories/{category_id}/hide", headers=_bearer(user.id))
    unhide_response = client.delete(f"/categories/{category_id}/hide", headers=_bearer(user.id))
    assert unhide_response.status_code == 204

    mine = {c["id"]: c["hidden"] for c in client.get("/categories", headers=_bearer(user.id)).json()}
    assert mine[category_id] is False


def test_hidden_category_still_appears_on_existing_expenses(client, db_session):
    """The story's own text: "hidden categories stop appearing in
    pickers while existing expenses keep them." Nothing in this
    endpoint touches expenses at all when hiding -- verified here that
    an expense referencing the hidden category is untouched."""
    user = _make_user(db_session, suffix="hide-expense")
    db_session.commit()
    category_id = _system_category_id(db_session)
    expense = _make_expense(db_session, user_id=user.id, category_id=uuid.UUID(category_id), suffix="kept")
    db_session.commit()

    client.post(f"/categories/{category_id}/hide", headers=_bearer(user.id))

    from app.models.expense import Expense

    row = db_session.get(Expense, expense.id)
    assert str(row.category_id) == category_id
