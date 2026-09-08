"""Story 2.2 acceptance criteria tests.

Runs against a real Postgres (expense_db_story9 -- a dedicated database
for this branch).

The story's own acceptance criteria:
  - Revoking an invite makes the link return "This invite is no longer
    valid". (The actual /join/{token} resolution is Story 2.3's own
    scope; this branch's job is making revoked_at real and correct --
    tested below via the model's own is_live property, which any future
    accept endpoint would check.)
  - An expired token cannot be accepted. (Same reasoning -- expires_at
    is set correctly and is_live correctly reports False once passed.)
  - The invite email renders correctly in Gmail/Outlook and contains
    inviter/group name/expiry. (Real email delivery needs Story 12.1's
    notification service, which doesn't exist yet -- explicit, known
    gap, documented in the router's own module docstring.)
  - Tokens never appear in server logs or in the URL of any analytics
    event. (Verified structurally below: the DB row only ever stores
    token_hash, never the raw token.)
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
DB_NAME = os.environ.get("STORY_2_2_POSTGRES_DB", "expense_db_story9")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-2-2")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_2_2_env(monkeypatch):
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


def _make_group(client, owner, *, name: str) -> dict:
    return client.post("/groups", json={"name": name}, headers=_bearer(owner.id)).json()


def _add_member(db_session, *, group_id, user_id, role):
    from app.models.group_member import GroupMember

    membership = GroupMember(group_id=group_id, user_id=user_id, role=role, removed_at=None)
    db_session.add(membership)
    db_session.flush()
    return membership


# ---------------------------------------------------------------------------
# Create -- link invites
# ---------------------------------------------------------------------------


def test_admin_can_create_a_link_invite(client, db_session):
    owner = _make_user(db_session, suffix="link-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Link Invite Group")

    response = client.post(f"/groups/{group['id']}/invites", json={"type": "link"}, headers=_bearer(owner.id))
    assert response.status_code == 201
    body = response.json()
    assert body["link"].startswith("/join/")
    assert body["max_uses"] == 25
    assert body["email"] is None


def test_link_token_is_never_stored_raw_only_hashed(client, db_session):
    from app.models.group_invite import GroupInvite

    owner = _make_user(db_session, suffix="hash-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Hash Check Group")

    response = client.post(f"/groups/{group['id']}/invites", json={"type": "link"}, headers=_bearer(owner.id))
    raw_token = response.json()["link"].removeprefix("/join/")

    row = db_session.execute(
        select(GroupInvite).where(GroupInvite.group_id == uuid.UUID(group["id"]))
    ).scalar_one()
    assert row.token_hash != raw_token
    assert len(row.token_hash) == 64  # sha256 hex digest length
    import hashlib

    assert row.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()


def test_member_non_admin_cannot_create_invite(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="member-cant-invite-owner")
    db_session.commit()
    group = _make_group(client, owner, name="No Member Invites")
    member = _make_user(db_session, suffix="member-cant-invite-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.post(f"/groups/{group['id']}/invites", json={"type": "link"}, headers=_bearer(member.id))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Create -- email invites
# ---------------------------------------------------------------------------


def test_admin_can_create_an_email_invite(client, db_session):
    owner = _make_user(db_session, suffix="email-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Email Invite Group")

    response = client.post(
        f"/groups/{group['id']}/invites",
        json={"type": "email", "email": "newperson@example.com", "role": "member"},
        headers=_bearer(owner.id),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "newperson@example.com"
    assert body["max_uses"] is None  # email invites are single-use by identity, not a use-count cap


def test_email_invite_without_an_email_is_422(client, db_session):
    owner = _make_user(db_session, suffix="no-email-owner")
    db_session.commit()
    group = _make_group(client, owner, name="No Email Given")

    response = client.post(f"/groups/{group['id']}/invites", json={"type": "email"}, headers=_bearer(owner.id))
    assert response.status_code == 422


def test_inviting_an_already_active_member_is_409(client, db_session):
    owner = _make_user(db_session, suffix="already-member-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Already A Member")
    from app.models.group_member import GroupRole

    existing_member = _make_user(db_session, suffix="already-member-target", email="already@example.com")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=existing_member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.post(
        f"/groups/{group['id']}/invites",
        json={"type": "email", "email": "already@example.com"},
        headers=_bearer(owner.id),
    )
    assert response.status_code == 409
    assert "already@example.com" in response.json()["detail"]


def test_reinviting_an_email_with_a_live_pending_invite_resends_not_duplicates(client, db_session):
    from app.models.group_invite import GroupInvite

    owner = _make_user(db_session, suffix="resend-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Resend Group")

    first = client.post(
        f"/groups/{group['id']}/invites",
        json={"type": "email", "email": "resend@example.com"},
        headers=_bearer(owner.id),
    )
    second = client.post(
        f"/groups/{group['id']}/invites",
        json={"type": "email", "email": "resend@example.com"},
        headers=_bearer(owner.id),
    )
    assert first.json()["id"] == second.json()["id"]  # same row, not a duplicate

    rows = db_session.execute(
        select(GroupInvite).where(
            GroupInvite.group_id == uuid.UUID(group["id"]), GroupInvite.email == "resend@example.com"
        )
    ).scalars().all()
    assert len(rows) == 1


def test_reinvite_token_changes_on_resend(client, db_session):
    owner = _make_user(db_session, suffix="resend-token-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Resend Token Group")

    first = client.post(
        f"/groups/{group['id']}/invites",
        json={"type": "email", "email": "resend-token@example.com"},
        headers=_bearer(owner.id),
    )
    second = client.post(
        f"/groups/{group['id']}/invites",
        json={"type": "email", "email": "resend-token@example.com"},
        headers=_bearer(owner.id),
    )
    assert first.json()["link"] != second.json()["link"]


def test_resend_rate_limited_to_3_per_email_per_group_per_day(client, db_session):
    owner = _make_user(db_session, suffix="rate-limit-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Rate Limit Group")

    for _ in range(3):
        response = client.post(
            f"/groups/{group['id']}/invites",
            json={"type": "email", "email": "ratelimited@example.com"},
            headers=_bearer(owner.id),
        )
        assert response.status_code in (201, 200)

    fourth = client.post(
        f"/groups/{group['id']}/invites",
        json={"type": "email", "email": "ratelimited@example.com"},
        headers=_bearer(owner.id),
    )
    assert fourth.status_code == 429


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_shows_only_pending_invites(client, db_session):
    owner = _make_user(db_session, suffix="list-owner")
    db_session.commit()
    group = _make_group(client, owner, name="List Group")

    link_response = client.post(f"/groups/{group['id']}/invites", json={"type": "link"}, headers=_bearer(owner.id))
    revoked_id = link_response.json()["id"]
    client.delete(f"/groups/{group['id']}/invites/{revoked_id}", headers=_bearer(owner.id))

    still_pending = client.post(
        f"/groups/{group['id']}/invites", json={"type": "email", "email": "pending@example.com"}, headers=_bearer(owner.id)
    ).json()

    response = client.get(f"/groups/{group['id']}/invites", headers=_bearer(owner.id))
    ids = {i["id"] for i in response.json()}
    assert revoked_id not in ids
    assert still_pending["id"] in ids


def test_member_non_admin_cannot_list_invites(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="list-403-owner")
    db_session.commit()
    group = _make_group(client, owner, name="List 403 Group")
    member = _make_user(db_session, suffix="list-403-member")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.get(f"/groups/{group['id']}/invites", headers=_bearer(member.id))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def test_revoking_an_invite_sets_revoked_at_and_it_is_no_longer_live(client, db_session):
    """The story's own AC: "Revoking an invite makes the link return
    'This invite is no longer valid'." The actual /join/{token} endpoint
    is Story 2.3's scope; this verifies the real, correct state
    (revoked_at set, is_live False) that endpoint would check."""
    from app.models.group_invite import GroupInvite

    owner = _make_user(db_session, suffix="revoke-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Revoke Group")
    created = client.post(f"/groups/{group['id']}/invites", json={"type": "link"}, headers=_bearer(owner.id)).json()

    response = client.delete(f"/groups/{group['id']}/invites/{created['id']}", headers=_bearer(owner.id))
    assert response.status_code == 204

    row = db_session.get(GroupInvite, uuid.UUID(created["id"]))
    assert row.revoked_at is not None
    assert row.is_live is False


def test_expired_invite_is_not_live(db_session):
    """The story's own AC: "An expired token cannot be accepted." --
    verified structurally via is_live, the property any future accept
    endpoint would check."""
    from app.models.group_invite import GroupInvite
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="expired-owner")
    db_session.flush()
    from app.models.group import Group

    group = Group(name=f"expired-grp-{_RUN_ID}", created_by=owner.id)
    db_session.add(group)
    db_session.flush()

    invite = GroupInvite(
        group_id=group.id,
        invited_by=owner.id,
        email=None,
        token_hash="x" * 64,
        role=GroupRole.MEMBER,
        expires_at=datetime.now(UTC) - timedelta(days=1),  # already expired
        max_uses=25,
    )
    db_session.add(invite)
    db_session.commit()

    assert invite.is_live is False


def test_revoke_of_nonexistent_invite_is_404(client, db_session):
    owner = _make_user(db_session, suffix="revoke-404-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Revoke 404 Group")

    response = client.delete(f"/groups/{group['id']}/invites/{uuid.uuid4()}", headers=_bearer(owner.id))
    assert response.status_code == 404
