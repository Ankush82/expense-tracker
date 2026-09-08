from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for every ORM model in this package.

    Every model here maps a class to a table Story 0.2's migration
    (api/alembic/versions/20260101_0000-0001_initial_schema.py)
    already created -- these classes describe that real schema, they
    never generate DDL of their own (Alembic owns migrations)."""
