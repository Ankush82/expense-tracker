"""Alembic environment configuration.

This wires Alembic into the same typed Settings the FastAPI app uses, so
migrations get the same DATABASE_URI the app will use at runtime (no risk
of DSN drift between app code and migrations). Only the schema-relevant
parts of the ORM (`Base.metadata`) are passed to `target_metadata`; we
deliberately do NOT import every model module here -- that would couple
every future model change to a working DB connection at upgrade time,
and the story 0.2 baseline deliberately ships only the migration, not
the SQLAlchemy ORM models (those land in their own stories).
"""
from logging.config import fileConfig
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `app.*` importable when alembic is run from any cwd.
_API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_API_DIR))

from app.core.config import settings  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime DB URL -- alembic.ini intentionally leaves it blank
# so we don't end up with two DSNs that can drift apart.
config.set_main_option("sqlalchemy.url", str(settings.DATABASE_URI))

# Story 0.2 ships only the raw migration -- no SQLAlchemy ORM models
# exist yet. A later story will import Base from a models package and
# wire target_metadata here.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (against a live DB connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()