"""Story 0.4 acceptance criteria tests.

The story's acceptance criteria are:
  - Integration tests prove a member cannot delete a group, a
    non-member gets 403 on every group endpoint, and a removed member
    loses access immediately on their next request.
  - /docs/permissions.md exists and matches the implemented behaviour.

Plus the explicit REQUIREMENTS section (get_current_user, require_group_role,
the expense ownership rule, visibility_filter) -- each gets its own real,
Postgres-backed test below, not just the two AC bullets verbatim.

Runs against the same real Postgres api/tests/test_story_0_2_migration.py
and tests/test_story_0_2_acceptance.py use (assumes the Story 0.2
migration is already applied -- these tests do not manage the schema
themselves, only the rows inside it).
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("POSTGRES_DB", "expense_db")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-0-4")

# users.google_sub/email are UNIQUE. Some of these tests exercise the real
# /groups endpoints through the `client` fixture, which runs its own,
# separate DB session (via get_db) -- rows created through it must be
# committed to be visible there, so db_session's own rollback-at-teardown
# can't clean them up (rollback after a commit is a no-op). A unique
# suffix per test RUN (not per test function) means a second run of this
# file, or the whole suite, never collides with a prior run's committed
# rows -- the same fix this project's own sibling app used for the
# identical real-DB-pollution risk.
_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_0_4_env(monkeypatch):
    """Same leak-prevention discipline test_health.py's own fixture
    documents: real, permanent os.environ writes here would bleed into
    whichever test file pytest happens to run next in the same
    session."""
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
    """One real session per test, rolled back (not just closed) at
    teardown so a test's own inserted rows never leak into a sibling
    test -- these tests don't share the module-scoped alembic_runner
    pattern test_story_0_2_acceptance.py uses, since nothing here
    touches the schema itself."""
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

    group = Group(name=f"group-{suffix}", created_by=owner_id)
    db_session.add(group)
    db_session.flush()
    return group


def _add_member(db_session, *, group_id, user_id, role, removed: bool = False):
    from app.models.group_member import GroupMember

    membership = GroupMember(
        group_id=group_id,
        user_id=user_id,
        role=role,
        removed_at=datetime.now(timezone.utc) if removed else None,
    )
    db_session.add(membership)
    db_session.flush()
    return membership


def _bearer(user_id: uuid.UUID) -> dict[str, str]:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------


def test_get_current_user_401_when_no_authorization_header(client):
    response = client.get(f"/groups/{uuid.uuid4()}")
    assert response.status_code == 401


def test_get_current_user_401_on_malformed_header(client):
    response = client.get(f"/groups/{uuid.uuid4()}", headers={"Authorization": "NotBearer abc"})
    assert response.status_code == 401


def test_get_current_user_401_on_garbage_token(client):
    response = client.get(f"/groups/{uuid.uuid4()}", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert response.status_code == 401


def test_get_current_user_401_on_expired_token(client, db_session):
    from app.core.security import create_access_token

    user = _make_user(db_session, suffix="expired")
    db_session.commit()
    expired_token = create_access_token(user.id, expires_delta=timedelta(seconds=-1))
    response = client.get(f"/groups/{uuid.uuid4()}", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 401


def test_get_current_user_401_when_token_names_a_nonexistent_user(client):
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET_KEY,
        algorithm="HS256",
    )
    response = client.get(f"/groups/{uuid.uuid4()}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_get_current_user_401_when_user_is_soft_deleted(client, db_session):
    user = _make_user(db_session, suffix="deleted-user")
    user.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    response = client.get(f"/groups/{uuid.uuid4()}", headers=_bearer(user.id))
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# require_group_role -- via the real /groups endpoints (integration, AC)
# ---------------------------------------------------------------------------


def test_non_member_gets_403_on_every_group_endpoint(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="owner-a")
    group = _make_group(db_session, owner_id=owner.id, suffix="a")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    stranger = _make_user(db_session, suffix="stranger-a")
    db_session.commit()

    get_response = client.get(f"/groups/{group.id}", headers=_bearer(stranger.id))
    assert get_response.status_code == 403

    delete_response = client.delete(f"/groups/{group.id}", headers=_bearer(stranger.id))
    assert delete_response.status_code == 403


def test_a_member_cannot_delete_a_group(client, db_session):
    """The story's own AC, verbatim: a member cannot delete a group."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="owner-b")
    group = _make_group(db_session, owner_id=owner.id, suffix="b")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    member = _make_user(db_session, suffix="member-b")
    _add_member(db_session, group_id=group.id, user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.delete(f"/groups/{group.id}", headers=_bearer(member.id))
    assert response.status_code == 403


def test_an_admin_also_cannot_delete_a_group_only_owner_can(client, db_session):
    """Not directly in the AC bullet, but the story's own role table
    (owner > admin > member) implies admin alone isn't enough for a
    DELETE gated at require_group_role(OWNER) -- worth pinning down
    explicitly so a future change to the route's minimum_role is a
    deliberate, reviewed choice, not a silent regression this suite
    misses."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="owner-c")
    group = _make_group(db_session, owner_id=owner.id, suffix="c")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    admin = _make_user(db_session, suffix="admin-c")
    _add_member(db_session, group_id=group.id, user_id=admin.id, role=GroupRole.ADMIN)
    db_session.commit()

    response = client.delete(f"/groups/{group.id}", headers=_bearer(admin.id))
    assert response.status_code == 403

    owner_response = client.delete(f"/groups/{group.id}", headers=_bearer(owner.id))
    assert owner_response.status_code == 204


def test_a_removed_member_loses_access_immediately(client, db_session):
    """The story's own AC, verbatim: a removed member loses access
    immediately on their next request -- no grace period, no stale
    cache."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="owner-d")
    group = _make_group(db_session, owner_id=owner.id, suffix="d")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    removed_user = _make_user(db_session, suffix="removed-d")
    _add_member(db_session, group_id=group.id, user_id=removed_user.id, role=GroupRole.MEMBER, removed=True)
    db_session.commit()

    response = client.get(f"/groups/{group.id}", headers=_bearer(removed_user.id))
    assert response.status_code == 403


def test_active_member_can_read_the_group(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="owner-e")
    group = _make_group(db_session, owner_id=owner.id, suffix="e")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    member = _make_user(db_session, suffix="member-e")
    _add_member(db_session, group_id=group.id, user_id=member.id, role=GroupRole.MEMBER)
    db_session.commit()

    response = client.get(f"/groups/{group.id}", headers=_bearer(member.id))
    assert response.status_code == 200
    assert response.json()["id"] == str(group.id)


# ---------------------------------------------------------------------------
# visibility_filter
# ---------------------------------------------------------------------------


def test_visibility_filter_shows_own_expenses_and_active_group_expenses_only(db_session):
    from app.core.security import visibility_filter
    from app.models.expense import Expense, ExpenseSource
    from app.models.group_member import GroupRole

    caller = _make_user(db_session, suffix="viz-caller")
    stranger = _make_user(db_session, suffix="viz-stranger")
    my_group = _make_group(db_session, owner_id=caller.id, suffix="viz-my")
    other_group = _make_group(db_session, owner_id=stranger.id, suffix="viz-other")
    _add_member(db_session, group_id=my_group.id, user_id=caller.id, role=GroupRole.MEMBER)
    # caller used to be a member of other_group, but was removed --
    # its expenses must NOT be visible.
    _add_member(db_session, group_id=other_group.id, user_id=caller.id, role=GroupRole.MEMBER, removed=True)

    own_expense = Expense(
        user_id=caller.id, group_id=None, amount_minor=100, currency="INR",
        merchant_raw="own", occurred_at=datetime.now(timezone.utc), source=ExpenseSource.MANUAL,
        status="confirmed",
    )
    group_expense = Expense(
        user_id=stranger.id, group_id=my_group.id, amount_minor=200, currency="INR",
        merchant_raw="shared", occurred_at=datetime.now(timezone.utc), source=ExpenseSource.MANUAL,
        status="confirmed",
    )
    invisible_expense = Expense(
        user_id=stranger.id, group_id=other_group.id, amount_minor=300, currency="INR",
        merchant_raw="not mine", occurred_at=datetime.now(timezone.utc), source=ExpenseSource.MANUAL,
        status="confirmed",
    )
    db_session.add_all([own_expense, group_expense, invisible_expense])
    # Story 11.2 tightened visibility_filter's own default: a group-mate's
    # expense is only visible to OTHER members once its owner has
    # explicitly set level=full for that group (Story 11.1's own default,
    # "aggregate", shows no individual expense rows to other members --
    # that WAS the real privacy gap 11.2 closes, not a regression this
    # test should paper over). An explicit full-visibility row for
    # stranger in my_group is what makes group_expense visible to caller
    # below, same as it would need to be through the real PUT
    # /groups/{id}/visibility endpoint.
    from app.models.member_visibility import MemberVisibility, VisibilityLevel

    db_session.add(
        MemberVisibility(group_id=my_group.id, user_id=stranger.id, level=VisibilityLevel.FULL)
    )
    db_session.commit()

    query = visibility_filter(select(Expense), caller, db_session)
    visible_ids = {row.id for row in db_session.execute(query).scalars()}

    assert own_expense.id in visible_ids
    assert group_expense.id in visible_ids
    assert invisible_expense.id not in visible_ids


# ---------------------------------------------------------------------------
# get_editable_expense_fields -- the resource-ownership rule
# ---------------------------------------------------------------------------


def test_expense_owner_may_edit_every_real_field(db_session):
    from app.core.security import _FULL_EDITABLE_EXPENSE_FIELDS, get_editable_expense_fields
    from app.models.expense import Expense, ExpenseSource

    owner = _make_user(db_session, suffix="exp-owner-a")
    expense = Expense(
        user_id=owner.id, group_id=None, amount_minor=100, currency="INR",
        merchant_raw="m", occurred_at=datetime.now(timezone.utc), source=ExpenseSource.MANUAL,
        status="confirmed",
    )
    db_session.add(expense)
    db_session.commit()

    fields = get_editable_expense_fields(owner, expense, db_session)
    assert fields == _FULL_EDITABLE_EXPENSE_FIELDS


def test_group_admin_may_only_edit_group_linkage_on_a_members_expense(db_session):
    """The story's own rule, verbatim: "a group admin may not edit
    another member's expense amount, only its group linkage."""
    from app.core.security import get_editable_expense_fields
    from app.models.expense import Expense, ExpenseSource
    from app.models.group_member import GroupRole

    group_owner = _make_user(db_session, suffix="exp-group-owner")
    member = _make_user(db_session, suffix="exp-member")
    group = _make_group(db_session, owner_id=group_owner.id, suffix="exp-g1")
    _add_member(db_session, group_id=group.id, user_id=group_owner.id, role=GroupRole.OWNER)
    _add_member(db_session, group_id=group.id, user_id=member.id, role=GroupRole.MEMBER)

    members_expense = Expense(
        user_id=member.id, group_id=group.id, amount_minor=500, currency="INR",
        merchant_raw="member's own", occurred_at=datetime.now(timezone.utc),
        source=ExpenseSource.MANUAL, status="confirmed",
    )
    db_session.add(members_expense)
    db_session.commit()

    fields = get_editable_expense_fields(group_owner, members_expense, db_session)
    assert fields == frozenset({"group_id"})


def test_plain_member_has_no_edit_rights_on_a_fellow_members_expense(db_session):
    from app.core.security import get_editable_expense_fields
    from app.models.expense import Expense, ExpenseSource
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="exp-owner-b")
    other_member = _make_user(db_session, suffix="exp-other-member")
    group = _make_group(db_session, owner_id=owner.id, suffix="exp-g2")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)
    _add_member(db_session, group_id=group.id, user_id=other_member.id, role=GroupRole.MEMBER)

    owners_expense = Expense(
        user_id=owner.id, group_id=group.id, amount_minor=700, currency="INR",
        merchant_raw="owner's own", occurred_at=datetime.now(timezone.utc),
        source=ExpenseSource.MANUAL, status="confirmed",
    )
    db_session.add(owners_expense)
    db_session.commit()

    fields = get_editable_expense_fields(other_member, owners_expense, db_session)
    assert fields is None


def test_non_member_of_the_expenses_group_has_no_edit_rights(db_session):
    from app.core.security import get_editable_expense_fields
    from app.models.expense import Expense, ExpenseSource
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="exp-owner-c")
    stranger = _make_user(db_session, suffix="exp-stranger")
    group = _make_group(db_session, owner_id=owner.id, suffix="exp-g3")
    _add_member(db_session, group_id=group.id, user_id=owner.id, role=GroupRole.OWNER)

    owners_expense = Expense(
        user_id=owner.id, group_id=group.id, amount_minor=900, currency="INR",
        merchant_raw="owner's own", occurred_at=datetime.now(timezone.utc),
        source=ExpenseSource.MANUAL, status="confirmed",
    )
    db_session.add(owners_expense)
    db_session.commit()

    fields = get_editable_expense_fields(stranger, owners_expense, db_session)
    assert fields is None


def test_personal_expense_with_no_group_is_editable_only_by_its_owner(db_session):
    from app.core.security import get_editable_expense_fields
    from app.models.expense import Expense, ExpenseSource

    owner = _make_user(db_session, suffix="exp-owner-d")
    someone_else = _make_user(db_session, suffix="exp-someone-else")
    personal_expense = Expense(
        user_id=owner.id, group_id=None, amount_minor=1000, currency="INR",
        merchant_raw="personal", occurred_at=datetime.now(timezone.utc),
        source=ExpenseSource.MANUAL, status="confirmed",
    )
    db_session.add(personal_expense)
    db_session.commit()

    assert get_editable_expense_fields(someone_else, personal_expense, db_session) is None


# ---------------------------------------------------------------------------
# /docs/permissions.md exists (AC)
# ---------------------------------------------------------------------------


def test_permissions_doc_exists_and_documents_the_real_functions():
    doc_path = Path(__file__).resolve().parent.parent / "docs" / "permissions.md"
    assert doc_path.is_file(), "/docs/permissions.md must exist (Story 0.4 AC)"
    text = doc_path.read_text()
    for symbol in ("require_group_role", "visibility_filter", "get_editable_expense_fields", "get_current_user"):
        assert symbol in text, f"/docs/permissions.md must document {symbol}()"
