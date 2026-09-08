"""Story 3.1 acceptance criteria tests.

Runs against a real Postgres (expense_db_story12 -- a dedicated database
for this branch, since the shared expense_db local instance already has
a different, unmerged branch's migration applied and this branch's own
migration chain doesn't include it; see the migration's own module
docstring). Assumes this branch's alembic chain is already at head.

The story's own acceptance criteria:
  - Sending the same Idempotency-Key twice yields one expense and two 200s.
  - All validation failures return 422 with a field-level error map, not
    a generic 500.
  - Deleting an expense removes it from lists and totals but the row
    survives with deleted_at set.
  - Another user requesting the expense by id gets 404 (not 403 -- do
    not leak existence).
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("STORY_3_1_POSTGRES_DB", "expense_db_story12")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-3-1")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_3_1_env(monkeypatch):
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


def _make_group(db_session, *, owner_id, suffix: str):
    from app.models.group import Group

    group = Group(name=f"group-{suffix}-{_RUN_ID}", created_by=owner_id)
    db_session.add(group)
    db_session.flush()
    return group


def _add_member(db_session, *, group_id, user_id, role, removed: bool = False):
    from app.models.group_member import GroupMember

    membership = GroupMember(
        group_id=group_id,
        user_id=user_id,
        role=role,
        removed_at=datetime.now(UTC) if removed else None,
    )
    db_session.add(membership)
    db_session.flush()
    return membership


def _bearer(user_id: uuid.UUID) -> dict[str, str]:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _system_category_id(db_session) -> str:
    from app.models.category import Category

    row = db_session.execute(select(Category).where(Category.is_system.is_(True)).limit(1)).scalar_one()
    return str(row.id)


def _valid_body(**overrides) -> dict:
    body = {
        "amount_minor": 5000,
        "currency": "INR",
        "merchant": "Corner Store",
        "occurred_at": datetime.now(UTC).isoformat(),
        "notes": "lunch",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_repeat_idempotency_key_within_24h_yields_one_expense_and_two_200s(client, db_session):
    user = _make_user(db_session, suffix="idem-a")
    db_session.commit()
    headers = {**_bearer(user.id), "Idempotency-Key": "retry-key-1"}

    first = client.post("/expenses", json=_valid_body(), headers=headers)
    second = client.post("/expenses", json=_valid_body(), headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    from app.models.expense import Expense

    count = db_session.execute(
        select(Expense).where(Expense.user_id == user.id, Expense.idempotency_key == "retry-key-1")
    ).scalars().all()
    assert len(count) == 1


def test_different_idempotency_keys_create_separate_expenses(client, db_session):
    user = _make_user(db_session, suffix="idem-b")
    db_session.commit()

    first = client.post("/expenses", json=_valid_body(), headers={**_bearer(user.id), "Idempotency-Key": "key-a"})
    second = client.post("/expenses", json=_valid_body(), headers={**_bearer(user.id), "Idempotency-Key": "key-b"})

    assert first.json()["id"] != second.json()["id"]


def test_post_without_idempotency_key_works_normally(client, db_session):
    user = _make_user(db_session, suffix="idem-c")
    db_session.commit()

    response = client.post("/expenses", json=_valid_body(), headers=_bearer(user.id))
    assert response.status_code == 200
    assert response.json()["merchant"] == "Corner Store"


# ---------------------------------------------------------------------------
# Validation -- 422 with a field-level error map, never a bare 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_minor": 0},
        {"amount_minor": -100},
        {"amount_minor": 100_000_000_001},
        {"currency": "ZZZ"},
        {"merchant": ""},
        {"merchant": "   "},
        {"merchant": "x" * 141},
        {"notes": "x" * 1001},
        {"occurred_at": (datetime.now(UTC) + timedelta(hours=25)).isoformat()},
        {"occurred_at": "1999-12-31T00:00:00+00:00"},
    ],
)
def test_validation_failures_return_422_with_field_level_detail(client, db_session, overrides):
    user = _make_user(db_session, suffix=f"val-{hash(str(overrides)) % 10_000}")
    db_session.commit()

    response = client.post("/expenses", json=_valid_body(**overrides), headers=_bearer(user.id))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert len(detail) >= 1
    assert "loc" in detail[0]


def test_category_id_not_owned_by_caller_is_a_422_not_a_500(client, db_session):
    other_user = _make_user(db_session, suffix="cat-other")
    from app.models.category import Category

    other_category = Category(owner_user_id=other_user.id, name="Not yours", is_system=False)
    db_session.add(other_category)
    db_session.flush()

    caller = _make_user(db_session, suffix="cat-caller")
    db_session.commit()

    response = client.post(
        "/expenses",
        json=_valid_body(category_id=str(other_category.id)),
        headers=_bearer(caller.id),
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "category_id"]


def test_system_category_id_is_accepted(client, db_session):
    user = _make_user(db_session, suffix="cat-sys")
    db_session.commit()
    category_id = _system_category_id(db_session)

    response = client.post("/expenses", json=_valid_body(category_id=category_id), headers=_bearer(user.id))
    assert response.status_code == 200
    assert response.json()["category_id"] == category_id


def test_group_id_caller_not_a_member_is_a_422_not_a_500(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="grp-owner")
    group = _make_group(db_session, owner_id=owner.id, suffix="grp-a")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    stranger = _make_user(db_session, suffix="grp-stranger")
    db_session.commit()

    response = client.post(
        "/expenses", json=_valid_body(group_id=str(group.id)), headers=_bearer(stranger.id)
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "group_id"]


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


def test_delete_soft_deletes_and_survives_but_is_excluded_from_lists(client, db_session):
    user = _make_user(db_session, suffix="del-a")
    db_session.commit()

    created = client.post("/expenses", json=_valid_body(), headers=_bearer(user.id)).json()
    expense_id = created["id"]

    delete_response = client.delete(f"/expenses/{expense_id}", headers=_bearer(user.id))
    assert delete_response.status_code == 204

    from app.models.expense import Expense

    row = db_session.get(Expense, uuid.UUID(expense_id))
    assert row is not None
    assert row.deleted_at is not None

    list_response = client.get("/expenses", headers=_bearer(user.id))
    ids_in_list = {item["id"] for item in list_response.json()["items"]}
    assert expense_id not in ids_in_list

    get_response = client.get(f"/expenses/{expense_id}", headers=_bearer(user.id))
    assert get_response.status_code == 404


def test_only_owner_may_delete(client, db_session):
    owner = _make_user(db_session, suffix="del-owner")
    other = _make_user(db_session, suffix="del-other")
    db_session.commit()

    created = client.post("/expenses", json=_valid_body(), headers=_bearer(owner.id)).json()

    response = client.delete(f"/expenses/{created['id']}", headers=_bearer(other.id))
    assert response.status_code == 404  # other user can't even see it -- not a shared group


def test_group_admin_cannot_delete_a_members_expense(client, db_session):
    """The story's own rule, verbatim: "Only the expense owner may edit
    or delete it. Group admins may not." """
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="del-grp-owner")
    group = _make_group(db_session, owner_id=owner.id, suffix="del-grp")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    member = _make_user(db_session, suffix="del-grp-member")
    _add_member(db_session, group_id=group.id, user_id=member.id, role=GroupRole.MEMBER)
    admin = _make_user(db_session, suffix="del-grp-admin")
    _add_member(db_session, group_id=group.id, user_id=admin.id, role=GroupRole.ADMIN)
    db_session.commit()

    created = client.post(
        "/expenses", json=_valid_body(group_id=str(group.id)), headers=_bearer(member.id)
    ).json()

    response = client.delete(f"/expenses/{created['id']}", headers=_bearer(admin.id))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Existence / visibility -- 404, never 403
# ---------------------------------------------------------------------------


def test_another_user_requesting_expense_by_id_gets_404_not_403(client, db_session):
    owner = _make_user(db_session, suffix="viz-owner")
    stranger = _make_user(db_session, suffix="viz-stranger")
    db_session.commit()

    created = client.post("/expenses", json=_valid_body(), headers=_bearer(owner.id)).json()

    response = client.get(f"/expenses/{created['id']}", headers=_bearer(stranger.id))
    assert response.status_code == 404


def test_owner_can_get_their_own_expense(client, db_session):
    owner = _make_user(db_session, suffix="viz-self")
    db_session.commit()

    created = client.post("/expenses", json=_valid_body(), headers=_bearer(owner.id)).json()

    response = client.get(f"/expenses/{created['id']}", headers=_bearer(owner.id))
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_fellow_group_member_can_see_but_not_edit_the_expense(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="viz-grp-owner")
    group = _make_group(db_session, owner_id=owner.id, suffix="viz-grp")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    payer = _make_user(db_session, suffix="viz-grp-payer")
    _add_member(db_session, group_id=group.id, user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    created = client.post(
        "/expenses", json=_valid_body(group_id=str(group.id)), headers=_bearer(payer.id)
    ).json()

    get_response = client.get(f"/expenses/{created['id']}", headers=_bearer(owner.id))
    assert get_response.status_code == 200

    patch_response = client.patch(
        f"/expenses/{created['id']}", json={"amount_minor": 999}, headers=_bearer(owner.id)
    )
    assert patch_response.status_code == 403


# ---------------------------------------------------------------------------
# Edit -- audit log, ownership
# ---------------------------------------------------------------------------


def test_edit_amount_writes_an_audit_log_entry(client, db_session):
    user = _make_user(db_session, suffix="audit-a")
    db_session.commit()

    created = client.post("/expenses", json=_valid_body(amount_minor=1000), headers=_bearer(user.id)).json()

    response = client.patch(
        f"/expenses/{created['id']}", json={"amount_minor": 2000}, headers=_bearer(user.id)
    )
    assert response.status_code == 200
    assert response.json()["amount_minor"] == 2000

    from app.models.audit_log import AuditLog

    entries = db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == "expense", AuditLog.entity_id == created["id"])
    ).scalars().all()
    assert len(entries) == 1
    assert entries[0].action == "update"
    assert entries[0].after["amount_minor"] == "2000"


def test_edit_notes_only_does_not_write_an_audit_log_entry(client, db_session):
    """Notes isn't in the story's audited-field list (amount, merchant,
    category, date) -- only those four trigger an audit_log row."""
    user = _make_user(db_session, suffix="audit-b")
    db_session.commit()

    created = client.post("/expenses", json=_valid_body(notes="original"), headers=_bearer(user.id)).json()

    client.patch(f"/expenses/{created['id']}", json={"notes": "updated"}, headers=_bearer(user.id))

    from app.models.audit_log import AuditLog

    entries = db_session.execute(
        select(AuditLog).where(AuditLog.entity_type == "expense", AuditLog.entity_id == created["id"])
    ).scalars().all()
    assert len(entries) == 0


def test_patch_by_non_owner_group_member_is_403(client, db_session):
    from app.models.group_member import GroupRole

    payer = _make_user(db_session, suffix="patch-payer")
    group = _make_group(db_session, owner_id=payer.id, suffix="patch-grp")
    _add_member(db_session, group_id=group.id, user_id=payer.id, role=GroupRole.OWNER)
    other_member = _make_user(db_session, suffix="patch-other")
    _add_member(db_session, group_id=group.id, user_id=other_member.id, role=GroupRole.MEMBER)
    db_session.commit()

    created = client.post(
        "/expenses", json=_valid_body(group_id=str(group.id)), headers=_bearer(payer.id)
    ).json()

    response = client.patch(
        f"/expenses/{created['id']}", json={"amount_minor": 1}, headers=_bearer(other_member.id)
    )
    assert response.status_code == 403


def test_patch_can_unset_group_id_back_to_a_personal_expense(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="patch-unset-owner")
    group = _make_group(db_session, owner_id=owner.id, suffix="patch-unset")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    db_session.commit()

    created = client.post(
        "/expenses", json=_valid_body(group_id=str(group.id)), headers=_bearer(owner.id)
    ).json()
    assert created["group_id"] == str(group.id)

    response = client.patch(
        f"/expenses/{created['id']}", json={"group_id": None}, headers=_bearer(owner.id)
    )
    assert response.status_code == 200
    assert response.json()["group_id"] is None


# ---------------------------------------------------------------------------
# List -- filters, pagination
# ---------------------------------------------------------------------------


def test_list_filters_by_group_id_and_category_id(client, db_session):
    from app.models.group_member import GroupRole

    user = _make_user(db_session, suffix="list-filter")
    group = _make_group(db_session, owner_id=user.id, suffix="list-filter")
    _add_member(db_session, group_id=group.id, user_id=user.id, role=GroupRole.OWNER)
    db_session.commit()
    category_id = _system_category_id(db_session)

    personal = client.post("/expenses", json=_valid_body(merchant="personal"), headers=_bearer(user.id)).json()
    grouped = client.post(
        "/expenses",
        json=_valid_body(merchant="grouped", group_id=str(group.id), category_id=category_id),
        headers=_bearer(user.id),
    ).json()

    by_group = client.get("/expenses", params={"group_id": str(group.id)}, headers=_bearer(user.id))
    ids = {item["id"] for item in by_group.json()["items"]}
    assert grouped["id"] in ids
    assert personal["id"] not in ids

    by_category = client.get("/expenses", params={"category_id": category_id}, headers=_bearer(user.id))
    ids = {item["id"] for item in by_category.json()["items"]}
    assert grouped["id"] in ids
    assert personal["id"] not in ids


def test_list_pagination_cursor_covers_all_items_with_no_duplicates(client, db_session):
    user = _make_user(db_session, suffix="list-page")
    db_session.commit()

    created_ids = set()
    for i in range(5):
        body = _valid_body(
            merchant=f"paged-{i}",
            occurred_at=(datetime.now(UTC) - timedelta(minutes=i)).isoformat(),
        )
        created = client.post("/expenses", json=body, headers=_bearer(user.id)).json()
        created_ids.add(created["id"])

    seen_ids: set[str] = set()
    cursor = None
    for _ in range(10):  # safety bound against an infinite loop on a real bug
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/expenses", params=params, headers=_bearer(user.id)).json()
        page_ids = [item["id"] for item in page["items"]]
        assert not (seen_ids & set(page_ids)), "pagination must never repeat an item across pages"
        seen_ids.update(page_ids)
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert created_ids <= seen_ids
