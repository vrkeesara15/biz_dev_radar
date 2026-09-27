"""The hand-written milestone migration must match the SQLAlchemy models exactly."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.core.db import Database
from app.models import Base
from sqlalchemy.engine import Connection


def _diff(connection: Connection) -> list[object]:
    ctx = MigrationContext.configure(
        connection, opts={"compare_type": True, "compare_server_default": False}
    )
    return compare_metadata(ctx, Base.metadata)


async def test_models_match_migrations(database: Database) -> None:
    async with database.owner_engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == [], "\n".join(repr(d) for d in diff)
