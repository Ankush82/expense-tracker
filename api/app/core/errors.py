"""Shared 422 field-level error helper. First introduced by Story 3.1
(app.schemas.expenses) for its own DB-level validation checks (category
ownership, group membership) that can't be expressed as a pydantic
field_validator; Story 3.4 needs the identical shape for its own
DB-level checks (parent-category nesting, per-user name uniqueness), so
it lives here instead of being duplicated per story."""
from __future__ import annotations

from fastapi import HTTPException, status


def field_error(field: str, msg: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=[{"loc": ["body", field], "msg": msg, "type": "value_error"}],
    )
