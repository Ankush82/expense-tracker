"""Story 3.3 acceptance criteria tests.

Runs against a real Postgres (expense_db_story14 -- a dedicated database
for this branch; see this project's other manual-work branches for why a
dedicated database, not the shared local expense_db, is used).

The story's own acceptance criteria:
  - Pasting 20 rows from Excel/Sheets populates 20 rows with dates and
    amounts parsed correctly for the user's locale. (The paste-parsing
    itself is a frontend concern -- no UI is implemented in this backend-
    only PR; POST /expenses/bulk accepting 20 well-formed rows and
    creating all 20 is the backend half of this AC, tested below.)
  - Submitting a batch where row 7 has a bad amount saves the other 19
    and leaves row 7 in the grid with an inline error.
  - A 100-row batch completes in under 3 seconds server-side.
"""
from __future__ import annotations

import os
import sys
import time
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
DB_NAME = os.environ.get("STORY_3_3_POSTGRES_DB", "expense_db_story14")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-3-3")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_3_3_env(monkeypatch):
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


def _row(**overrides) -> dict:
    row = {
        "amount_minor": 1000,
        "currency": "INR",
        "merchant": "Corner Store",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    return row


def test_20_well_formed_rows_all_created(client, db_session):
    user = _make_user(db_session, suffix="bulk-20")
    db_session.commit()

    items = [_row(merchant=f"Merchant {i}", amount_minor=1000 + i) for i in range(20)]
    response = client.post("/expenses/bulk", json={"items": items}, headers=_bearer(user.id))
    assert response.status_code == 200
    body = response.json()
    assert len(body["created"]) == 20
    assert body["failed"] == []


def test_bad_row_in_the_middle_does_not_block_the_others(client, db_session):
    """The story's own example, verbatim: row 7 (index 6) has a bad
    amount; the other 19 still save, row 7 comes back in `failed`."""
    user = _make_user(db_session, suffix="bulk-bad-row")
    db_session.commit()

    items = [_row(merchant=f"Merchant {i}") for i in range(20)]
    items[6]["amount_minor"] = -500  # bad: must be > 0

    response = client.post("/expenses/bulk", json={"items": items}, headers=_bearer(user.id))
    assert response.status_code == 200
    body = response.json()
    assert len(body["created"]) == 19
    assert len(body["failed"]) == 1
    assert body["failed"][0]["index"] == 6
    assert body["failed"][0]["errors"][0]["loc"] == ["body", "amount_minor"]

    created_merchants = {item["merchant"] for item in body["created"]}
    assert "Merchant 6" not in created_merchants
    assert "Merchant 5" in created_merchants
    assert "Merchant 7" in created_merchants


def test_multiple_bad_rows_report_every_failure_independently(client, db_session):
    user = _make_user(db_session, suffix="bulk-multi-bad")
    db_session.commit()

    items = [_row(merchant="ok-1"), _row(merchant="", amount_minor=1000), _row(merchant="ok-2", amount_minor=0)]
    response = client.post("/expenses/bulk", json={"items": items}, headers=_bearer(user.id))
    body = response.json()
    assert len(body["created"]) == 1
    assert {f["index"] for f in body["failed"]} == {1, 2}


def test_invalid_category_id_fails_only_that_row(client, db_session):
    owner = _make_user(db_session, suffix="bulk-cat-owner")
    stranger = _make_user(db_session, suffix="bulk-cat-stranger")
    db_session.commit()
    from app.models.category import Category

    owners_category = Category(owner_user_id=owner.id, name="Owner Only", is_system=False)
    db_session.add(owners_category)
    db_session.commit()

    items = [_row(merchant="valid"), _row(merchant="bad-cat", category_id=str(owners_category.id))]
    response = client.post("/expenses/bulk", json={"items": items}, headers=_bearer(stranger.id))
    body = response.json()
    assert len(body["created"]) == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["index"] == 1
    assert body["failed"][0]["errors"][0]["loc"] == ["body", "category_id"]


def test_group_id_caller_not_a_member_fails_only_that_row(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="bulk-grp-owner")
    from app.models.group import Group

    group = Group(name=f"grp-{_RUN_ID}", created_by=owner.id)
    db_session.add(group)
    db_session.flush()
    from app.models.group_member import GroupMember

    db_session.add(GroupMember(group_id=group.id, user_id=owner.id, role=GroupRole.OWNER))
    stranger = _make_user(db_session, suffix="bulk-grp-stranger")
    db_session.commit()

    items = [_row(merchant="valid"), _row(merchant="bad-group", group_id=str(group.id))]
    response = client.post("/expenses/bulk", json={"items": items}, headers=_bearer(stranger.id))
    body = response.json()
    assert len(body["created"]) == 1
    assert body["failed"][0]["index"] == 1
    assert body["failed"][0]["errors"][0]["loc"] == ["body", "group_id"]


def test_more_than_100_rows_is_rejected_as_a_whole_request(client, db_session):
    user = _make_user(db_session, suffix="bulk-over-100")
    db_session.commit()

    items = [_row(merchant=f"m{i}") for i in range(101)]
    response = client.post("/expenses/bulk", json={"items": items}, headers=_bearer(user.id))
    assert response.status_code == 422


def test_empty_batch_is_rejected(client, db_session):
    user = _make_user(db_session, suffix="bulk-empty")
    db_session.commit()

    response = client.post("/expenses/bulk", json={"items": []}, headers=_bearer(user.id))
    assert response.status_code == 422


def test_100_row_batch_completes_under_3_seconds(client, db_session):
    user = _make_user(db_session, suffix="bulk-perf")
    db_session.commit()
    items = [_row(merchant=f"perf-{i}", amount_minor=100 + i) for i in range(100)]

    start = time.monotonic()
    response = client.post("/expenses/bulk", json={"items": items}, headers=_bearer(user.id))
    elapsed = time.monotonic() - start

    assert response.status_code == 200
    assert len(response.json()["created"]) == 100
    assert elapsed < 3.0, f"100-row batch took {elapsed:.2f}s, story requires under 3s"


def test_created_expenses_are_real_rows_visible_via_list(client, db_session):
    user = _make_user(db_session, suffix="bulk-visible")
    db_session.commit()
    items = [_row(merchant="check-visible-1"), _row(merchant="check-visible-2")]

    bulk_response = client.post("/expenses/bulk", json={"items": items}, headers=_bearer(user.id))
    created_ids = {e["id"] for e in bulk_response.json()["created"]}

    list_response = client.get("/expenses", headers=_bearer(user.id))
    listed_ids = {e["id"] for e in list_response.json()["items"]}
    assert created_ids <= listed_ids


def test_no_extra_fields_allowed_at_the_request_level(client, db_session):
    user = _make_user(db_session, suffix="bulk-extra")
    db_session.commit()

    response = client.post(
        "/expenses/bulk", json={"items": [_row()], "unexpected": "field"}, headers=_bearer(user.id)
    )
    assert response.status_code == 422
