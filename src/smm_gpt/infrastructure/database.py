"""SQLAlchemy engine and PostgreSQL readiness probe."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base metadata for all future tenant-scoped persistence models."""


class Database:
    """Own the shared async engine and its lifecycle."""

    name = "postgresql"

    def __init__(self, url: str, timeout_seconds: float) -> None:
        self._engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self._timeout_seconds = timeout_seconds

    async def ping(self) -> None:
        async with asyncio.timeout(self._timeout_seconds):
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            yield session

    async def close(self) -> None:
        await self._engine.dispose()
