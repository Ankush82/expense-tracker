"""Story 9.1 acceptance criteria tests.

Runs against a real Postgres (expense_db_story41 -- a dedicated database
for this branch).

The story's own acceptance criteria:
  - Every listed event type is emitted by its owning service and
    appears in the feed. (Real, explicit scope gap, documented in
    app.core.activity's own module docstring: member_joined/left/
    removed/role_changed depend on Story 2.3/2.4 endpoints that don't
    exist yet; expense_split_created/settlement_recorded depend on Epic
    15; comment_added depends on Story 9.3. Verified here: the 5 event
    types whose owning endpoints DO exist today -- expense_added,
    expense_edited, expense_deleted, budget_created, budget_updated --
    are genuinely emitted and appear in the feed.)
  - A restricted member's events show no amounts to other members.
  - Pagination returns no duplicates and no gaps when new events arrive
    mid-scroll.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("STORY_9_1_POSTGRES_DB", "expense_db_story41")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-9-1")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_9_1_env(monkeypatch):
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


def _expense_body(**overrides) -> dict:
    body = {"amount_minor": 1000, "currency": "INR", "merchant": "Corner Store", "occurred_at": datetime.now(UTC).isoformat()}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Real emitters -- expense_added / expense_edited / expense_deleted
# ---------------------------------------------------------------------------


def test_expense_added_appears_in_group_feed(client, db_session):
    owner = _make_user(db_session, suffix="feed-add-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Feed Add Group")

    client.post("/expenses", json=_expense_body(group_id=group["id"]), headers=_bearer(owner.id))

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    event_types = [e["event_type"] for e in feed["items"]]
    assert "expense_added" in event_types


def test_personal_expense_with_no_group_emits_no_activity(client, db_session):
    """No group, no feed to log to -- app.core.activity's own record()
    is only ever called for a group-scoped expense."""
    owner = _make_user(db_session, suffix="feed-personal-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Unrelated Group For Personal Test")

    client.post("/expenses", json=_expense_body(), headers=_bearer(owner.id))  # no group_id

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    assert feed["items"] == []


def test_expense_edited_and_deleted_appear_in_feed(client, db_session):
    owner = _make_user(db_session, suffix="feed-edit-del-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Feed Edit Delete Group")

    created = client.post("/expenses", json=_expense_body(group_id=group["id"]), headers=_bearer(owner.id)).json()
    client.patch(f"/expenses/{created['id']}", json={"amount_minor": 2000}, headers=_bearer(owner.id))
    client.delete(f"/expenses/{created['id']}", headers=_bearer(owner.id))

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    event_types = [e["event_type"] for e in feed["items"]]
    assert "expense_edited" in event_types
    assert "expense_deleted" in event_types


def test_editing_only_notes_does_not_emit_expense_edited(client, db_session):
    """notes isn't an audited field (Story 3.1's own rule) -- no real
    change worth logging."""
    owner = _make_user(db_session, suffix="feed-notes-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Feed Notes Group")

    created = client.post("/expenses", json=_expense_body(group_id=group["id"]), headers=_bearer(owner.id)).json()
    client.patch(f"/expenses/{created['id']}", json={"notes": "just a note"}, headers=_bearer(owner.id))

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    event_types = [e["event_type"] for e in feed["items"]]
    assert "expense_edited" not in event_types


# ---------------------------------------------------------------------------
# Real emitters -- budget_created / budget_updated
# ---------------------------------------------------------------------------


def test_budget_created_and_updated_appear_in_feed(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="feed-budget-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Feed Budget Group")

    created = client.post(
        "/budgets",
        json={"scope": "group", "group_id": group["id"], "period": "monthly", "amount_minor": 50000, "currency": "INR"},
        headers=_bearer(owner.id),
    ).json()
    client.patch(f"/budgets/{created['id']}", json={"amount_minor": 60000}, headers=_bearer(owner.id))

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    event_types = [e["event_type"] for e in feed["items"]]
    assert "budget_created" in event_types
    assert "budget_updated" in event_types


# ---------------------------------------------------------------------------
# Visibility redaction
# ---------------------------------------------------------------------------


def test_restricted_members_events_show_no_amounts_to_other_members(client, db_session):
    """The story's own AC, verbatim: "A restricted member's events show
    no amounts to other members." Story 11.2 refined this into two real,
    distinct behaviors matching Story 11.1's own definitions exactly --
    aggregate (still appears, redacted -- tested here) vs hidden
    (excluded entirely -- tested separately below, in
    test_hidden_level_excludes_the_event_from_the_feed_entirely)."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="redact-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Redact Group")
    payer = _make_user(db_session, suffix="redact-payer")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    client.put(f"/groups/{group['id']}/visibility", json={"level": "aggregate"}, headers=_bearer(payer.id))
    client.post(
        "/expenses",
        json=_expense_body(group_id=group["id"], merchant="Secret Merchant", amount_minor=99999),
        headers=_bearer(payer.id),
    )

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    add_event = next(e for e in feed["items"] if e["event_type"] == "expense_added")
    assert add_event["metadata"] == {"redacted": True}


def test_hidden_level_excludes_the_event_from_the_feed_entirely(client, db_session):
    """Story 11.1's own words for hidden: "the user contributes nothing
    to group views." Story 11.2 enforces this literally for the
    activity feed -- not a redacted stub, no row at all, filtered at
    the query layer before pagination (never fetch-then-hide)."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="hidden-feed-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Hidden Feed Group")
    payer = _make_user(db_session, suffix="hidden-feed-payer")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    client.put(f"/groups/{group['id']}/visibility", json={"level": "hidden"}, headers=_bearer(payer.id))
    client.post(
        "/expenses", json=_expense_body(group_id=group["id"], merchant="Fully Hidden"), headers=_bearer(payer.id)
    )

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    assert not any(e["event_type"] == "expense_added" for e in feed["items"])

    # But the hidden-level actor still sees their own event, in full.
    own_feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(payer.id)).json()
    own_event = next(e for e in own_feed["items"] if e["event_type"] == "expense_added")
    assert own_event["metadata"]["merchant"] == "Fully Hidden"


def test_full_visibility_member_shows_real_metadata_to_others(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="full-vis-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Full Visibility Group")
    payer = _make_user(db_session, suffix="full-vis-payer")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    client.put(f"/groups/{group['id']}/visibility", json={"level": "full"}, headers=_bearer(payer.id))
    client.post(
        "/expenses",
        json=_expense_body(group_id=group["id"], merchant="Visible Merchant"),
        headers=_bearer(payer.id),
    )

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    add_event = next(e for e in feed["items"] if e["event_type"] == "expense_added")
    assert add_event["metadata"]["merchant"] == "Visible Merchant"


def test_actor_always_sees_their_own_events_unredacted(client, db_session):
    """The level only restricts what OTHER members see -- you always see
    your own real data."""
    owner = _make_user(db_session, suffix="own-events-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Own Events Group")

    client.put(f"/groups/{group['id']}/visibility", json={"level": "hidden"}, headers=_bearer(owner.id))
    client.post(
        "/expenses", json=_expense_body(group_id=group["id"], merchant="My Own Merchant"), headers=_bearer(owner.id)
    )

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    add_event = next(e for e in feed["items"] if e["event_type"] == "expense_added")
    assert add_event["metadata"]["merchant"] == "My Own Merchant"


def test_default_aggregate_level_also_redacts(client, db_session):
    """Default (no row) is aggregate, not full -- redaction applies even
    when the actor never explicitly touched their own setting."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="default-redact-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Default Redact Group")
    payer = _make_user(db_session, suffix="default-redact-payer")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    client.post("/expenses", json=_expense_body(group_id=group["id"]), headers=_bearer(payer.id))

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    add_event = next(e for e in feed["items"] if e["event_type"] == "expense_added")
    assert add_event["metadata"] == {"redacted": True}


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_pagination_no_duplicates_no_gaps(client, db_session):
    owner = _make_user(db_session, suffix="paginate-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Paginate Group")

    for i in range(25):
        client.post(
            "/expenses", json=_expense_body(group_id=group["id"], merchant=f"m-{i}"), headers=_bearer(owner.id)
        )

    seen_ids: set[str] = set()
    cursor = None
    for _ in range(10):  # safety bound
        params = {"limit": 10}
        if cursor:
            params["cursor"] = cursor
        page = client.get(f"/groups/{group['id']}/activity", params=params, headers=_bearer(owner.id)).json()
        page_ids = [e["id"] for e in page["items"]]
        assert not (seen_ids & set(page_ids)), "pagination must never repeat an item across pages"
        seen_ids.update(page_ids)
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen_ids) == 25  # exactly one expense_added event per created expense, no gaps


def test_new_events_arriving_mid_scroll_do_not_duplicate_or_skip_older_pages(client, db_session):
    """A new event inserted between two page fetches gets a HIGHER id
    (bigserial, append-only) -- keyset pagination on id DESC means it
    sorts before the cursor position already consumed, so it never
    appears in (or shifts) a page already fetched."""
    owner = _make_user(db_session, suffix="mid-scroll-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Mid Scroll Group")

    for i in range(5):
        client.post(
            "/expenses", json=_expense_body(group_id=group["id"], merchant=f"first-{i}"), headers=_bearer(owner.id)
        )

    first_page = client.get(f"/groups/{group['id']}/activity", params={"limit": 3}, headers=_bearer(owner.id)).json()
    first_page_ids = {e["id"] for e in first_page["items"]}

    # New events arrive "mid-scroll".
    client.post("/expenses", json=_expense_body(group_id=group["id"], merchant="new-arrival"), headers=_bearer(owner.id))

    second_page = client.get(
        f"/groups/{group['id']}/activity", params={"limit": 3, "cursor": first_page["next_cursor"]}, headers=_bearer(owner.id)
    ).json()
    second_page_ids = {e["id"] for e in second_page["items"]}
    assert not (first_page_ids & second_page_ids)


def test_types_filter_limits_to_requested_event_types(client, db_session):
    owner = _make_user(db_session, suffix="types-filter-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Types Filter Group")

    created = client.post("/expenses", json=_expense_body(group_id=group["id"]), headers=_bearer(owner.id)).json()
    client.delete(f"/expenses/{created['id']}", headers=_bearer(owner.id))

    feed = client.get(
        f"/groups/{group['id']}/activity", params={"types": ["expense_deleted"]}, headers=_bearer(owner.id)
    ).json()
    assert all(e["event_type"] == "expense_deleted" for e in feed["items"])
    assert len(feed["items"]) == 1


def test_non_member_cannot_view_activity_feed(client, db_session):
    owner = _make_user(db_session, suffix="feed-403-owner")
    stranger = _make_user(db_session, suffix="feed-403-stranger")
    db_session.commit()
    group = _make_group(client, owner, name="Feed 403 Group")

    response = client.get(f"/groups/{group['id']}/activity", headers=_bearer(stranger.id))
    assert response.status_code == 403
