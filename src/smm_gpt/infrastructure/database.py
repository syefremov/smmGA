"""SQLAlchemy engine and PostgreSQL readiness probe."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

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
        self._engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True, hide_parameters=True
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self._timeout_seconds = timeout_seconds

    async def ping(self) -> None:
        async with asyncio.timeout(self._timeout_seconds):
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

    async def require_restricted_role(self) -> None:
        async with self.session() as session:
            unsafe = await session.scalar(
                text(
                    "SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb "
                    "OR EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' "
                    "AND tableowner=current_user) FROM pg_roles WHERE rolname=current_user"
                )
            )
            if unsafe is not False:
                raise RuntimeError("A non-owner, non-privileged runtime database role is required")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            yield session

    @asynccontextmanager
    async def transaction(
        self, user_id: UUID | None = None, workspace_id: UUID | None = None
    ) -> AsyncIterator[AsyncSession]:
        """Commit on success, rollback on error; tenant context never escapes the transaction."""
        async with self.session() as session, session.begin():
            await session.execute(
                text(
                    "SELECT set_config('smm.user_id', :user, true), "
                    "set_config('smm.workspace_id', :workspace, true)"
                ),
                {
                    "user": str(user_id) if user_id else "",
                    "workspace": str(workspace_id) if workspace_id else "",
                },
            )
            yield session

    async def close(self) -> None:
        await self._engine.dispose()
