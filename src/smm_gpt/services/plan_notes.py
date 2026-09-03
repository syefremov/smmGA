"""Shared, explicitly disclosed planning notes; no private run/input queries."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.plan_adoption import PlanNotesView
from smm_gpt.infrastructure.ai_models import PlanNotes
from smm_gpt.infrastructure.content_models import ContentRecord
from smm_gpt.services.access import AccessService
from smm_gpt.services.content_records import record


async def notes_view(s: AsyncSession, wid: UUID, pid: UUID) -> PlanNotesView | None:
    requested = await record(s, wid, pid, "content_plan")
    # Notes stay attached to their immutable version. A descendant sees the nearest ancestor,
    # explicitly as history, never silently loses its gaps or claims current validation.
    row = await s.scalar(
        select(PlanNotes)
        .join(
            ContentRecord,
            (ContentRecord.workspace_id == PlanNotes.workspace_id)
            & (ContentRecord.id == PlanNotes.plan_id),
        )
        .where(
            PlanNotes.workspace_id == wid,
            ContentRecord.family_id == requested.family_id,
            ContentRecord.number <= requested.number,
        )
        .order_by(ContentRecord.number.desc())
        .limit(1)
    )
    if row is None:
        return None
    if canonical_hash(row.body) != row.content_hash:
        raise OperationError("plan_notes_integrity_error")
    return PlanNotesView.model_validate(
        {
            **{
                key: getattr(row, key)
                for key in (
                    "id",
                    "plan_id",
                    "plan_hash",
                    "content_hash",
                    "actor_id",
                    "created_at",
                    "body",
                )
            },
            "requested_plan_id": pid,
            "exact_version": pid == row.plan_id,
        }
    )


async def require_inherited_gaps(s: AsyncSession, wid: UUID, pid: UUID, gaps: list[str]) -> None:
    notes = await notes_view(s, wid, pid)
    if notes and any(gap not in gaps for gap in notes.body.knowledge_gaps):
        raise OperationError("planner_inherited_gaps_required")


class PlanNotesService:
    def __init__(self, access: AccessService):
        self.access = access

    async def read(
        self, actor: Principal, wid: UUID, pid: UUID, request: UUID
    ) -> PlanNotesView | None:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            return await notes_view(s, wid, pid)
