"""The database connection the tools use.

One engine, pointed at the READ-ONLY role. On top of the role's own limits,
every session asks Postgres for a read-only transaction and a statement
timeout, so even a misconfigured DATABASE_URL (say, the admin role) cannot
turn a tool into a write path.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

log = logging.getLogger("sales_mcp.db")

_engine: AsyncEngine | None = None


def sqlalchemy_url(url: str) -> str:
    """Accept a plain postgresql:// URL and use the asyncpg driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def asyncpg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def configure(url: str) -> AsyncEngine:
    global _engine
    _engine = create_async_engine(
        sqlalchemy_url(url),
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "15000",
                "application_name": "sales-mcp",
            }
        },
    )
    return _engine


async def dispose() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def read_only_session() -> AsyncIterator[AsyncSession]:
    if _engine is None:
        raise RuntimeError("Database not configured: call sales_mcp.db.configure() first.")
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        yield session


async def warn_if_writable() -> bool:
    """Log a warning when the configured role could write. Returns True if so.

    The read-only session settings still protect the data, but a role with
    write grants means the guarantee rests on one layer instead of two.
    """
    async with read_only_session() as session:
        writable = (
            await session.execute(
                text("SELECT has_table_privilege(current_user, 'store.sale', 'INSERT')")
            )
        ).scalar()
    if writable:
        log.warning(
            "The MCP is connected with a role that can WRITE. Point DATABASE_URL at "
            "the read-only role created by `sales-mcp setup`."
        )
    return bool(writable)
