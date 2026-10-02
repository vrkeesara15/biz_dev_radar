"""Alembic environment: async engine on the OWNER role (DATABASE_URL_OWNER)."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

import app.models  # noqa: F401  (registers every table on Base.metadata)
from alembic import context
from app.models.base import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # Settings reads DATABASE_URL_OWNER itself and normalizes managed-Postgres URLs
    # (postgres:// / postgresql:// -> postgresql+asyncpg://, sslmode -> ssl), so the
    # async engine never falls back to the psycopg driver that the image omits.
    from app.core.config import get_settings

    return get_settings().database_url_owner


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
