from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(str(settings.DATABASE_URI), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding one real request-scoped session,
    closed unconditionally in `finally` so a raised HTTPException from
    a downstream dependency (get_current_user's 401, require_group_role's
    403) still releases the connection back to the pool."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
