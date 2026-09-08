"""Story 11.2: an explicit, reviewed registry of every route that
returns per-expense (or per-activity-event) group data, paired with the
real coverage test in tests/test_story_11_2_acceptance.py.

Deliberately NOT auto-detected via pure static analysis -- for a real
security control, "does this route correctly enforce visibility" is a
human judgment call (calling `visibility_filter` doesn't by itself PROVE
correctness, only that the recognized mechanism was invoked at all).
What IS automated, and enforced in CI: the coverage test inspects every
registered FastAPI route's source for a reference to the `Expense` or
`GroupActivity` models and asserts it also appears here. A new
group-scoped route that queries either model and is NOT added to this
registry fails that test -- exactly the story's own AC ("Adding a new
group-scoped endpoint without registering it in the filter fails CI").
Adding a route here is a real, reviewable declaration: "a human
confirmed this route enforces Story 11.1/11.2 visibility correctly,"
not just "some code path happens to mention Expense."
"""

# (HTTP method, path template exactly as FastAPI registers it)
EXPENSE_VISIBILITY_ENFORCED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/expenses"),  # creates the caller's own row -- always visible to them
        ("POST", "/expenses/bulk"),  # same -- every created row belongs to the caller
        ("GET", "/expenses"),
        ("GET", "/expenses/{expense_id}"),
        ("PATCH", "/expenses/{expense_id}"),
        ("DELETE", "/expenses/{expense_id}"),
    }
)

ACTIVITY_VISIBILITY_ENFORCED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/groups/{group_id}/activity"),
    }
)

# Real, reviewed exception: these two routes reference the Expense model
# (a bulk UPDATE setting group_id/category_id to NULL when a group or
# category is deleted -- Story 2.1's real unlink-not-cascade rule, Story
# 3.4's real reassignment rule) but never RETURN any expense field to
# the caller, so there is no visibility leak to enforce against here --
# confirmed by reading both handlers directly. Listed explicitly rather
# than silently excluded, so the coverage test below still forces a
# human to re-look at this file if either handler's real behavior ever
# changes to start returning expense data.
_WRITE_ONLY_EXPENSE_TOUCHING_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("DELETE", "/groups/{group_id}"),
        ("DELETE", "/categories/{category_id}"),
    }
)
