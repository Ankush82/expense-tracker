# Permissions (Story 0.4)

This document is the real, current behaviour implemented by
`api/app/core/security.py`. If this file and the code ever disagree,
the code is correct and this file is out of date — fix this file.

## Identity and sessions

Every authenticated request carries `Authorization: Bearer <JWT>`. The
token's `sub` claim is the caller's `users.id` (UUID); `exp` is a
standard JWT expiry. `get_current_user()` verifies the signature
(HS256, `SECRET_KEY`), checks expiry, and resolves `sub` to a real,
non-deleted `User` row. Any failure — missing header, malformed token,
expired token, invalid signature, or a `sub` naming a user that
doesn't exist (or was soft-deleted) — is a **401**, never a 403 or 404.
A caller cannot distinguish "your token is bad" from "that account is
gone" from the status code alone.

## Group roles

`group_members.role` is one of, in ascending privilege:

| Role   | Rank |
|--------|------|
| member | 1    |
| admin  | 2    |
| owner  | 3    |

A membership is **active** iff `removed_at IS NULL`. A removed member
is treated identically to someone who was never a member at all —
same 403, immediately, on their very next request. There is no grace
period.

## Group-scoped endpoints: `require_group_role(minimum_role)`

Any route with a `group_id` path parameter can require
`Depends(require_group_role(GroupRole.X))`. The caller must be an
**active** member of that exact `group_id` with role rank >= X's rank.

| Action | Minimum role | Non-member / removed member |
|---|---|---|
| View a group | member | 403 |
| Delete a group | owner | 403 |

A non-member and a removed member get the **same 403** — group
existence is not distinguishable from lack of access via the status
code (this differs from the expense-lookup rule below, which
deliberately uses 404 instead; see Epic 3's own story for why: an
expense's *existence* is itself sensitive, a group's is not once you
already have its id).

## Expense visibility: `visibility_filter()`

Every query that returns `Expense` rows to a caller **must** pass
through `visibility_filter(query, current_user, db)` — no endpoint may
write its own ad-hoc `WHERE` clause for this. An expense is visible to
a caller iff:

- the caller is the expense's own `user_id`, **or**
- the expense has a non-null `group_id`, and the caller is currently
  an **active** member of that group (any role).

Epic 11 (Visibility Settings) will extend this function's body with
finer-grained per-group/per-user visibility rules later. Every future
caller keeps calling `visibility_filter()` by name — extending Epic 11
means editing this one function, not auditing every endpoint that
queries expenses.

## Expense edit rights: `get_editable_expense_fields()`

| Caller relationship to the expense | Editable fields |
|---|---|
| Is the expense's own `user_id` | `amount_minor`, `currency`, `merchant_raw`, `category_id`, `occurred_at`, `notes`, `group_id` (all real mutable fields) |
| Active `admin` or `owner` of the expense's own `group_id` (not the expense owner) | `group_id` only — "group linkage," never the amount, merchant, category, date, or notes |
| Active plain `member` of the expense's own `group_id` (not the expense owner) | none — no edit access (may still *see* it via `visibility_filter`) |
| Not an active member of the expense's `group_id`, and not its owner | none |
| Expense has no `group_id` (personal) and caller isn't its owner | none |

This is the story's own rule verbatim: *"a group admin may not edit
another member's expense amount, only its group linkage."* This
implementation reads "admin" as rank >= `admin` (so `owner` gets the
same restricted right on a fellow member's expense that `admin`
does) — an owner is not automatically the same as the expense's own
`user_id`, so this rule applies to them too unless they happen to be
the expense's actual owner.

`get_editable_expense_fields()` returns the exact set of field names
a caller may change, or `None` for no access at all. Story 3.1's real
PATCH endpoint is expected to reject (422/403, its own choice) any
request that names a field outside the returned set.

## Editing a settled split

Editing an expense that is part of a **settled** split (Epic 15) must
be blocked with `409`, telling the caller to unsettle first. Epic 15
does not exist yet as of this story — `get_editable_expense_fields()`
has no concept of "settled" today. Epic 15's own story must add that
check (whether inside this function, given a settled-status input, or
as an additional check its own endpoint performs after calling this
function) — recorded here so it isn't missed.

## Audit logging

Editing an expense's `amount_minor`, `merchant_raw`, `category_id`, or
`occurred_at` must write an `audit_log` row. This story defines the
`AuditLog` model (`api/app/models/audit_log.py`) but does not itself
insert any rows — Story 3.1's real PATCH endpoint, which is the only
code path that actually performs an edit, is responsible for the
insert.
