"""Story 11.1 acceptance criteria tests.

Runs against a real Postgres (expense_db_story47 -- a dedicated database
for this branch).

The story's own acceptance criteria:
  - Default on join is aggregate.
  - Switching to hidden removes the member from all group breakdowns
    within one request cycle. (The actual group-dashboard/breakdown
    endpoints are Epic 7's own scope, not built yet -- this branch's
    job, tested below, is that the setting itself changes synchronously
    and is immediately visible on the next GET, which is exactly what a
    real dashboard endpoint would need to read fresh.)
  - An owner attempting to change another member's setting gets 403.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("STORY_11_1_POSTGRES_DB", "expense_db_story47")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-11-1")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_11_1_env(monkeypatch):
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


# ---------------------------------------------------------------------------
# Default
# ---------------------------------------------------------------------------


def test_default_is_aggregate_with_no_row(client, db_session):
    """The story's own AC: default on join is aggregate. No group-join
    endpoint exists yet (Story 2.3) to actually create a real row, so
    this is the real, current source of truth for "a member's setting
    before they've ever touched it": a GET with no row returns the
    default, not a 404 or a null level."""
    owner = _make_user(db_session, suffix="default-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Default Level Group")

    response = client.get(f"/groups/{group['id']}/visibility", headers=_bearer(owner.id))
    assert response.status_code == 200
    body = response.json()
    assert body["level"] == "aggregate"
    assert body["hide_categories"] == []
    assert body["updated_at"] is None  # no row exists yet -- a real, meaningful distinction


def test_non_member_cannot_get_visibility(client, db_session):
    owner = _make_user(db_session, suffix="nonmember-owner")
    stranger = _make_user(db_session, suffix="nonmember-stranger")
    db_session.commit()
    group = _make_group(client, owner, name="Private Visibility Group")

    response = client.get(f"/groups/{group['id']}/visibility", headers=_bearer(stranger.id))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Update -- own setting, immediate
# ---------------------------------------------------------------------------


def test_member_can_set_their_own_level_to_hidden(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="set-hidden-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Set Hidden Group")
    member = _make_user(db_session, suffix="set-hidden-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.put(f"/groups/{group['id']}/visibility", json={"level": "hidden"}, headers=_bearer(member.id))
    assert response.status_code == 200
    assert response.json()["level"] == "hidden"


def test_switching_to_hidden_is_immediately_visible_on_next_get(client, db_session):
    """The story's own AC: "Switching to hidden removes the member from
    all group breakdowns within one request cycle." Verified at this
    story's own real scope: the change is synchronous, no cache, visible
    on the very next request -- exactly what a future breakdown endpoint
    (Epic 7, not built yet) would need."""
    owner = _make_user(db_session, suffix="immediate-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Immediate Group")

    before = client.get(f"/groups/{group['id']}/visibility", headers=_bearer(owner.id)).json()
    assert before["level"] == "aggregate"

    client.put(f"/groups/{group['id']}/visibility", json={"level": "hidden"}, headers=_bearer(owner.id))

    after = client.get(f"/groups/{group['id']}/visibility", headers=_bearer(owner.id)).json()
    assert after["level"] == "hidden"
    assert after["updated_at"] is not None


def test_updating_persists_hide_categories(client, db_session):
    owner = _make_user(db_session, suffix="hide-cat-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Hide Categories Group")
    cat_id = str(uuid.uuid4())

    response = client.put(
        f"/groups/{group['id']}/visibility",
        json={"level": "full", "hide_categories": [cat_id]},
        headers=_bearer(owner.id),
    )
    assert response.status_code == 200
    assert response.json()["hide_categories"] == [cat_id]


def test_put_twice_updates_the_same_row_not_a_duplicate(client, db_session):
    from app.models.member_visibility import MemberVisibility
    from sqlalchemy import select

    owner = _make_user(db_session, suffix="upsert-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Upsert Group")

    client.put(f"/groups/{group['id']}/visibility", json={"level": "full"}, headers=_bearer(owner.id))
    client.put(f"/groups/{group['id']}/visibility", json={"level": "hidden"}, headers=_bearer(owner.id))

    rows = db_session.execute(
        select(MemberVisibility).where(
            MemberVisibility.group_id == uuid.UUID(group["id"]), MemberVisibility.user_id == owner.id
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].level == "hidden"


# ---------------------------------------------------------------------------
# Owner cannot override a member's setting
# ---------------------------------------------------------------------------


def test_owner_attempting_to_change_another_members_setting_gets_403(client, db_session):
    """The story's own AC, verbatim."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="override-owner")
    db_session.commit()
    group = _make_group(client, owner, name="No Override Group")
    member = _make_user(db_session, suffix="override-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.put(
        f"/groups/{group['id']}/visibility",
        json={"level": "full", "user_id": str(member.id)},
        headers=_bearer(owner.id),
    )
    assert response.status_code == 403

    # And the member's own setting was genuinely untouched.
    member_view = client.get(f"/groups/{group['id']}/visibility", headers=_bearer(member.id)).json()
    assert member_view["level"] == "aggregate"  # still the default -- never changed


def test_put_with_own_user_id_explicitly_named_still_works(client, db_session):
    owner = _make_user(db_session, suffix="self-name-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Self Name Group")

    response = client.put(
        f"/groups/{group['id']}/visibility",
        json={"level": "hidden", "user_id": str(owner.id)},
        headers=_bearer(owner.id),
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_summary_shows_every_active_members_level_but_not_hide_categories(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="summary-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Summary Group")
    member = _make_user(db_session, suffix="summary-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    client.put(f"/groups/{group['id']}/visibility", json={"level": "hidden"}, headers=_bearer(member.id))

    response = client.get(f"/groups/{group['id']}/visibility/summary", headers=_bearer(owner.id))
    assert response.status_code == 200
    levels_by_user = {m["user_id"]: m["level"] for m in response.json()["members"]}
    assert levels_by_user[str(owner.id)] == "aggregate"  # owner never set anything -- real default
    assert levels_by_user[str(member.id)] == "hidden"
    for member_row in response.json()["members"]:
        assert "hide_categories" not in member_row


def test_member_non_admin_can_still_view_summary(client, db_session):
    """The summary is readable by any active member, not just admins --
    "so the UI can explain why data is missing" applies to every
    member's own dashboard, not just an admin's."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="summary-member-view-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Summary Member View Group")
    member = _make_user(db_session, suffix="summary-member-view-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.get(f"/groups/{group['id']}/visibility/summary", headers=_bearer(member.id))
    assert response.status_code == 200
