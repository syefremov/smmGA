"""Shared bounded transitions for ingestion; never an AI retry policy."""

from typing import Literal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.file_models import KnowledgeFile
from smm_gpt.infrastructure.knowledge_models import KnowledgeIndex
from smm_gpt.infrastructure.models import Identity, Membership, User, utcnow


async def allowed(s: AsyncSession, wid: UUID, actor: UUID, identity: UUID) -> bool:
    return bool(
        await s.scalar(
            select(Identity.id)
            .join(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Identity.id == identity,
                Identity.user_id == actor,
                Identity.active.is_(True),
                User.active.is_(True),
                Membership.workspace_id == wid,
                Membership.active.is_(True),
                Membership.role.in_(["owner", "editor", "strategist"]),
            )
        )
    )


def finish(row: KnowledgeFile | KnowledgeIndex, state: str, error: str | None) -> None:
    row.state, row.error_code = state, error
    row.version += 1
    row.finished_at, row.lease_until = utcnow(), None


async def reconcile(database: Database, kind: Literal["index", "file"]) -> int:
    async with database.transaction() as s:
        return int(
            await s.scalar(text("SELECT public.smm_ingestion_reconcile(:kind)"), {"kind": kind})
            or 0
        )
