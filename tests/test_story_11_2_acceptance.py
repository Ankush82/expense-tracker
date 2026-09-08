"""Story 11.2 acceptance criteria tests.

Runs against a real Postgres (expense_db_story48 -- a dedicated database
for this branch).

The story's own acceptance criteria:
  - An automated test matrix covers 3 visibility levels x 6 endpoint
    families and asserts the exact fields returned in each combination.
    (Real, honest scope: only 2 of the 6 endpoint families the story
    lists -- analytics, expense lists, activity feed, comments,
    digests, exports, search -- exist as real endpoints today: expense
    lists and the activity feed. The other 4 depend on stories not
    built yet. This test matrix covers both real families x all 3
    levels, and documents the gap for the rest.)
  - Raw API responses for a restricted member contain no merchant or
    amount fields at all -- not nulls, not masked strings, absent.
  - Adding a new group-scoped endpoint without registering it in the
    filter fails CI.
"""
from __future__ import annotations

import inspect
import os
import sys
import uuid
from datetime import UTC
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(_API_DIR))

DB_HOST = os.environ.get("POSTGRES_SERVER", "localhost")
DB_USER = os.environ.get("POSTGRES_USER", "expense_user")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "expense_pass")
DB_NAME = os.environ.get("STORY_11_2_POSTGRES_DB", "expense_db_story48")
SECRET_KEY = os.environ.get("SECRET_KEY", "test-secret-key-for-story-11-2")

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _story_11_2_env(monkeypatch):
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


def _set_level(client, db_session, *, group_id, user_id, level: str | None):
    """None means "leave at the real default (no row)" -- aggregate."""
    if level is None:
        return
    # Set via the actual user's own token so this exercises the real
    # PUT endpoint, not a direct DB write.
    from app.core.security import create_access_token

    client.put(
        f"/groups/{group_id}/visibility",
        json={"level": level},
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )


def _expense_body(**overrides) -> dict:
    from datetime import datetime

    body = {"amount_minor": 4200, "currency": "INR", "merchant": "Test Merchant", "occurred_at": datetime.now(UTC).isoformat()}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Coverage registry -- the story's own AC: "Adding a new group-scoped
# endpoint without registering it in the filter fails CI."
# ---------------------------------------------------------------------------


def test_every_route_touching_expense_or_activity_models_is_registered():
    """Real, automated coverage check: walks every registered FastAPI
    route, and for any whose handler's own source references the
    Expense or GroupActivity models, asserts it's declared in
    app.core.visibility_registry. A NEW route added later that queries
    either model without being added to the registry fails this test --
    exactly the story's own AC. This does not (and cannot, for a genuine
    security control) verify the route enforces visibility CORRECTLY --
    only that a human explicitly reviewed and declared it does, via
    being listed in the registry at all (see the registry module's own
    docstring for why that's a deliberate choice, not a shortcut)."""
    from app.core.visibility_registry import (
        ACTIVITY_VISIBILITY_ENFORCED_ROUTES,
        EXPENSE_VISIBILITY_ENFORCED_ROUTES,
        _WRITE_ONLY_EXPENSE_TOUCHING_ROUTES,
    )
    from app.main import app

    registered = (
        EXPENSE_VISIBILITY_ENFORCED_ROUTES | ACTIVITY_VISIBILITY_ENFORCED_ROUTES | _WRITE_ONLY_EXPENSE_TOUCHING_ROUTES
    )
    unregistered_violations = []

    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if endpoint is None or methods is None or path is None:
            continue
        try:
            source = inspect.getsource(endpoint)
        except (OSError, TypeError):
            continue
        touches_expense_data = "Expense" in source or "GroupActivity" in source
        if not touches_expense_data:
            continue
        for method in methods:
            if method == "HEAD":
                continue
            if (method, path) not in registered:
                unregistered_violations.append((method, path))

    assert unregistered_violations == [], (
        f"route(s) touch Expense/GroupActivity but are not in app.core.visibility_registry: "
        f"{unregistered_violations}"
    )


def test_registered_routes_actually_exist():
    """The inverse check -- a registry entry for a route that was
    renamed or removed is a real, silent gap (the coverage test above
    would never catch it, since it only checks routes that DO exist)."""
    from app.core.visibility_registry import (
        ACTIVITY_VISIBILITY_ENFORCED_ROUTES,
        EXPENSE_VISIBILITY_ENFORCED_ROUTES,
        _WRITE_ONLY_EXPENSE_TOUCHING_ROUTES,
    )
    from app.main import app

    real_routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods is None or path is None:
            continue
        for method in methods:
            if method != "HEAD":
                real_routes.add((method, path))

    all_registered = EXPENSE_VISIBILITY_ENFORCED_ROUTES | ACTIVITY_VISIBILITY_ENFORCED_ROUTES | _WRITE_ONLY_EXPENSE_TOUCHING_ROUTES
    for entry in all_registered:
        assert entry in real_routes, f"registered route {entry} no longer exists"


# ---------------------------------------------------------------------------
# The real matrix: 3 levels x 2 real endpoint families (expense list,
# activity feed) -- exact fields present/absent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["full", "aggregate", "hidden"])
def test_expense_list_matrix(client, db_session, level):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix=f"matrix-list-owner-{level}")
    db_session.commit()
    group = _make_group(client, owner, name=f"Matrix List {level}")
    payer = _make_user(db_session, suffix=f"matrix-list-payer-{level}")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    _set_level(client, db_session, group_id=group["id"], user_id=payer.id, level=level)
    client.post("/expenses", json=_expense_body(group_id=group["id"], merchant="Matrix Merchant"), headers=_bearer(payer.id))

    listing = client.get("/expenses", params={"group_id": group["id"]}, headers=_bearer(owner.id)).json()
    payer_items = [e for e in listing["items"]]

    if level == "full":
        assert len(payer_items) == 1
        assert payer_items[0]["merchant"] == "Matrix Merchant"
    else:
        # aggregate (explicit or, for the None/default case tested
        # separately below, implicit) and hidden both mean: no
        # individual expense row for another member in a raw list.
        assert payer_items == []


def test_expense_list_default_no_row_behaves_like_aggregate(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="matrix-list-default-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Matrix List Default")
    payer = _make_user(db_session, suffix="matrix-list-default-payer")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    client.post("/expenses", json=_expense_body(group_id=group["id"]), headers=_bearer(payer.id))

    listing = client.get("/expenses", params={"group_id": group["id"]}, headers=_bearer(owner.id)).json()
    assert listing["items"] == []


def test_expense_get_by_id_matrix_full_visible_others_404(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="matrix-get-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Matrix Get Group")
    payer = _make_user(db_session, suffix="matrix-get-payer")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    created = client.post("/expenses", json=_expense_body(group_id=group["id"]), headers=_bearer(payer.id)).json()

    # Default (aggregate) -- 404, never a 403 (existence must never leak).
    assert client.get(f"/expenses/{created['id']}", headers=_bearer(owner.id)).status_code == 404

    _set_level(client, db_session, group_id=group["id"], user_id=payer.id, level="full")
    assert client.get(f"/expenses/{created['id']}", headers=_bearer(owner.id)).status_code == 200


@pytest.mark.parametrize("level", ["full", "aggregate", "hidden"])
def test_activity_feed_matrix(client, db_session, level):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix=f"matrix-feed-owner-{level}")
    db_session.commit()
    group = _make_group(client, owner, name=f"Matrix Feed {level}")
    payer = _make_user(db_session, suffix=f"matrix-feed-payer-{level}")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    _set_level(client, db_session, group_id=group["id"], user_id=payer.id, level=level)
    client.post("/expenses", json=_expense_body(group_id=group["id"], merchant="Feed Matrix Merchant"), headers=_bearer(payer.id))

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    add_events = [e for e in feed["items"] if e["event_type"] == "expense_added"]

    if level == "full":
        assert len(add_events) == 1
        assert add_events[0]["metadata"]["merchant"] == "Feed Matrix Merchant"
    elif level == "aggregate":
        assert len(add_events) == 1
        assert add_events[0]["metadata"] == {"redacted": True}
        assert "merchant" not in add_events[0]["metadata"]
    else:  # hidden
        assert add_events == []


# ---------------------------------------------------------------------------
# "not nulls, not masked strings, absent"
# ---------------------------------------------------------------------------


def test_redacted_activity_event_has_no_merchant_or_amount_keys_at_all(client, db_session):
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="absent-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Absent Fields Group")
    payer = _make_user(db_session, suffix="absent-payer")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    client.post("/expenses", json=_expense_body(group_id=group["id"]), headers=_bearer(payer.id))  # default aggregate

    feed = client.get(f"/groups/{group['id']}/activity", headers=_bearer(owner.id)).json()
    add_event = next(e for e in feed["items"] if e["event_type"] == "expense_added")
    # Not null, not masked -- the keys themselves are absent.
    assert "merchant" not in add_event["metadata"]
    assert "amount_minor" not in add_event["metadata"]
    assert "currency" not in add_event["metadata"]
    assert add_event["metadata"] == {"redacted": True}


def test_viewer_own_visibility_setting_never_affects_what_they_see_of_others(client, db_session):
    """Story 11.1's own rule: the viewer always sees their own data in
    full, and their OWN setting is irrelevant to what they see of
    OTHERS -- only the acting/owning member's own level matters."""
    from app.models.group_member import GroupRole

    owner = _make_user(db_session, suffix="viewer-setting-owner")
    db_session.commit()
    group = _make_group(client, owner, name="Viewer Setting Group")
    payer = _make_user(db_session, suffix="viewer-setting-payer")
    _add_member(db_session, group_id=uuid.UUID(group["id"]), user_id=payer.id, role=GroupRole.MEMBER)
    db_session.commit()

    # Owner (the viewer) sets themselves to hidden -- irrelevant to what
    # they see of payer, who is full.
    _set_level(client, db_session, group_id=group["id"], user_id=owner.id, level="hidden")
    _set_level(client, db_session, group_id=group["id"], user_id=payer.id, level="full")
    client.post("/expenses", json=_expense_body(group_id=group["id"], merchant="Visible To Hidden Viewer"), headers=_bearer(payer.id))

    listing = client.get("/expenses", params={"group_id": group["id"]}, headers=_bearer(owner.id)).json()
    assert any(e["merchant"] == "Visible To Hidden Viewer" for e in listing["items"])
