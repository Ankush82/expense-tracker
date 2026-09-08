"""Story 4.2 — Gmail sender selection: WHICH senders a sync queries
for, never HOW the sync itself runs (Story 4.3's job) or how a raw
message gets parsed (Story 5.1's job, keyed by `parser_key`).

We never scan the whole mailbox. `build_gmail_query()` is the one real
function that turns the curated `transaction_senders` registry (plus a
caller's own per-user allow/block overrides) into a narrow, auditable
Gmail search query."""
from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.transaction_sender import TransactionSender
from app.models.user_sender_override import SenderOverrideAction, UserSenderOverride

logger = logging.getLogger(__name__)


def _active_sender_patterns_for_user(db: Session, user_id: uuid.UUID) -> set[str]:
    """The real, effective sender set for one user: every active global
    registry pattern, PLUS this user's own `allow` overrides (even for
    a pattern the global registry doesn't carry), MINUS this user's own
    `block` overrides -- a block always wins, even over an active
    global entry (the story's own AC: "blocking a sender stops future
    ingestion from it within one sync cycle" -- the very next call to
    this function reflects it, since nothing here caches)."""
    global_patterns = set(
        db.execute(
            select(TransactionSender.sender_pattern).where(TransactionSender.is_active.is_(True))
        ).scalars()
    )

    overrides = db.execute(
        select(UserSenderOverride.sender_pattern, UserSenderOverride.action).where(
            UserSenderOverride.user_id == user_id
        )
    ).all()
    allowed = {pattern for pattern, action in overrides if action == SenderOverrideAction.ALLOW}
    blocked = {pattern for pattern, action in overrides if action == SenderOverrideAction.BLOCK}

    return (global_patterns | allowed) - blocked


def build_gmail_query(db: Session, user_id: uuid.UUID, last_sync: date) -> str:
    """Builds `(from:a OR from:b OR ...) after:{last_sync} -category:promotions`
    -- the story's own exact, required shape. Returns an always-false
    query (`"-* in:anywhere"`, matches nothing) when the effective
    sender set is empty, rather than ever falling back to a bare
    full-mailbox fetch (the story's own explicit "never" rule) or
    raising, since "this user has zero senders configured" is a real,
    legal state (a brand-new user before any sender is confirmed).

    Logs the constructed QUERY ONLY -- never any result -- so support
    can reproduce a user's sync (the story's own AC). The user_id is
    logged too (structured, not interpolated into the query text
    itself) so a specific user's real sync can be found in the logs."""
    patterns = _active_sender_patterns_for_user(db, user_id)

    if not patterns:
        query = "-* in:anywhere"
    else:
        from_clause = " OR ".join(f"from:{pattern}" for pattern in sorted(patterns))
        query = f"({from_clause}) after:{last_sync.strftime('%Y/%m/%d')} -category:promotions"

    logger.info(
        "email ingestion query built",
        extra={"user_id": str(user_id), "gmail_query": query},
    )
    return query
