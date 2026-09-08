"""Story 0.4 — centralized authorization: no endpoint reinvents this.

Because users can see each other's spending, authorization is a real
product feature here, not an implementation detail (the story's own
framing). Everything a future endpoint needs to answer "is this caller
allowed to do this" lives in this one module:

  - get_current_user(): resolves a bearer JWT to a real, live User row.
    401 when the token is missing, malformed, expired, or names a user
    that no longer exists (soft-deleted or actually gone).
  - require_group_role(minimum_role): a dependency FACTORY. The
    returned dependency 403s unless the caller is an ACTIVE member
    (removed_at IS NULL) of the path's group_id with role rank >=
    minimum_role's rank. Role order: owner > admin > member.
  - visibility_filter(): the single function every query returning
    expenses must go through to restrict results to what the caller
    may see (Epic 11's privacy settings will extend this later, not
    replace it).
  - get_editable_expense_fields(): the resource-ownership rule --
    see /docs/permissions.md for the full matrix this implements.

Story 1.1 (Google OAuth sign-in) is what will actually ISSUE these
tokens; this story builds the verification side against the same
contract (HS256, signed with settings.SECRET_KEY, `sub` = the user's
UUID as a string, `exp` = standard JWT expiry) so the two stories
compose without either one waiting on the other. create_access_token()
below is the real, shared implementation both this story's own tests
and Story 1.1 can call.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.expense import Expense
from app.models.group_member import GroupMember, GroupRole
from app.models.user import User

_JWT_ALGORITHM = "HS256"

# Role order for require_group_role's `minimum_role` comparison. A plain
# Python Enum has no built-in ordering, so this is the one real,
# reviewable place that order is declared -- everything else in this
# module (and any future caller) compares ranks via this dict, never by
# comparing GroupRole members directly.
_ROLE_RANK: dict[GroupRole, int] = {
    GroupRole.MEMBER: 1,
    GroupRole.ADMIN: 2,
    GroupRole.OWNER: 3,
}

# The real, mutable fields on Expense a full edit is allowed to touch
# (Story 3.1 owns the actual PATCH endpoint; this is the authorization
# layer's own declaration of what "edit" means for the ownership rule
# below, not that endpoint's request schema).
_FULL_EDITABLE_EXPENSE_FIELDS = frozenset(
    {"amount_minor", "currency", "merchant_raw", "category_id", "occurred_at", "notes", "group_id"}
)
# What a group admin/owner (not the expense's own owner) may change on
# a fellow member's expense: "only its group linkage" -- the story's
# own exact words.
_GROUP_LINKAGE_ONLY_FIELDS = frozenset({"group_id"})


def create_access_token(user_id: uuid.UUID, *, expires_delta: timedelta | None = None) -> str:
    """Real, shared token issuance -- Story 1.1's Google OAuth callback
    calls this after resolving/creating the User row; this story's own
    tests call it to obtain a real, valid token rather than hand-rolling
    JWT bytes and silently drifting from whatever Story 1.1 later does."""
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_JWT_ALGORITHM)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolves the `Authorization: Bearer <token>` header to a real,
    live User row. Raises 401 -- never any other status -- for every
    real failure mode: header absent, malformed, wrong scheme, expired
    token, invalid signature, or a `sub` that names no real (or a
    soft-deleted) user. A caller must never be able to distinguish
    "your token is bad" from "you don't exist anymore" from the status
    code alone; both are real, deliberate 401s."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise _unauthorized("missing or malformed Authorization header")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise _unauthorized("missing bearer token")

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[_JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("token expired")
    except jwt.InvalidTokenError:
        raise _unauthorized("invalid token")

    raw_sub = payload.get("sub")
    try:
        user_id = uuid.UUID(str(raw_sub))
    except (TypeError, ValueError):
        raise _unauthorized("token carries no valid subject")

    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise _unauthorized("user does not exist")
    return user


def require_group_role(minimum_role: GroupRole):
    """Dependency FACTORY (call it, don't pass it bare):
    `Depends(require_group_role(GroupRole.ADMIN))`.

    The returned dependency reads `group_id` from the request's own
    path parameters (every group-scoped route must declare a
    `group_id` path param for this to find), then 403s unless the
    caller is an ACTIVE member (removed_at IS NULL) whose role's rank
    is >= minimum_role's rank. A non-member and a removed member get
    the identical 403 -- this deliberately does not distinguish
    "never was a member" from "was removed", so a removed member's
    next request is treated exactly like a stranger's, immediately
    (the story's own AC)."""

    def _dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> GroupMember:
        raw_group_id = request.path_params.get("group_id")
        if raw_group_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="require_group_role used on a route with no group_id path parameter",
            )
        try:
            group_id = uuid.UUID(str(raw_group_id))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")

        membership = db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == current_user.id,
                GroupMember.removed_at.is_(None),
            )
        ).scalar_one_or_none()

        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not an active member of this group")
        if _ROLE_RANK[GroupRole(membership.role)] < _ROLE_RANK[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires role {minimum_role.value} or higher",
            )
        return membership

    return _dependency


def _active_group_ids_for_user(db: Session, user_id: uuid.UUID) -> Select:
    """Subquery: every group_id `user_id` is an ACTIVE member of. A
    subquery (not a materialized list) so visibility_filter composes
    into one real SQL statement rather than round-tripping twice."""
    return select(GroupMember.group_id).where(
        GroupMember.user_id == user_id, GroupMember.removed_at.is_(None)
    )


def visibility_filter(query: Select, current_user: User, db: Session) -> Select:
    """The single function every query returning `Expense` rows must
    go through to restrict results to what `current_user` may see
    (the story's own requirement -- "no ad-hoc filtering in endpoint
    code"). Visible = the caller's own expenses, OR any expense
    attached to a group the caller is currently an active member of.
    Epic 11 (privacy settings) will extend this function's body later;
    every future caller keeps calling the same name, so that story
    doesn't require touching every endpoint that filters expenses."""
    member_group_ids = _active_group_ids_for_user(db, current_user.id)
    return query.where(
        or_(
            Expense.user_id == current_user.id,
            Expense.group_id.in_(member_group_ids),
        )
    )


def get_editable_expense_fields(current_user: User, expense: Expense, db: Session) -> frozenset[str] | None:
    """Resource-ownership rule (see /docs/permissions.md for the full
    matrix): the expense's own owner may edit every real mutable
    field. A group admin or owner (rank >= ADMIN) of the expense's
    OWN group_id may edit only `group_id` itself -- "may not edit
    another member's expense amount, only its group linkage", the
    story's own exact words. Anyone else (a plain member, or an
    admin of a DIFFERENT group than the expense's own) gets None: no
    edit access at all, though visibility_filter may still let them
    *see* the expense if they share a group with it.

    Returns:
        frozenset[str]: the exact field names this caller may change.
        None: no edit access whatsoever.
    """
    if expense.user_id == current_user.id:
        return _FULL_EDITABLE_EXPENSE_FIELDS

    if expense.group_id is None:
        return None  # a personal expense is editable only by its owner

    membership = db.execute(
        select(GroupMember).where(
            GroupMember.group_id == expense.group_id,
            GroupMember.user_id == current_user.id,
            GroupMember.removed_at.is_(None),
        )
    ).scalar_one_or_none()

    if membership is None:
        return None
    if _ROLE_RANK[GroupRole(membership.role)] < _ROLE_RANK[GroupRole.ADMIN]:
        return None  # a plain member has no edit rights on someone else's expense

    return _GROUP_LINKAGE_ONLY_FIELDS


def require_global_admin(current_user: User = Depends(get_current_user)) -> User:
    """Story 4.2: gates the admin endpoint for adding to the global
    `transaction_senders` registry. No other story has defined a real
    global-admin role/table yet, so this is deliberately the simplest
    real mechanism available -- an env var allowlist
    (settings.admin_emails), matched case-insensitively against the
    caller's own real email. 403s (not 404 -- admin-only endpoints
    existing is not itself sensitive) for anyone not on the list."""
    if current_user.email.lower() not in settings.admin_emails:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin access required")
    return current_user
