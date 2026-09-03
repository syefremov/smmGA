"""Personal human-confirmed transfer, with immutable shared notes and private provenance."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import content as c
from smm_gpt.domain import plan_adoption as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.planner import PlanDraft, PlanningContext
from smm_gpt.infrastructure.ai_models import AIArtifact, AIRun, PlanAdoption, PlanNotes
from smm_gpt.infrastructure.content_models import ContentLink
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.ai_queue import current_input
from smm_gpt.services.content_records import create_record
from smm_gpt.services.knowledge import lock
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.planner import validate_draft
from smm_gpt.services.profiles import assert_registered_run


def candidate(draft: PlanDraft, context: PlanningContext) -> tuple[c.ContentPlan, d.PlanNotesBody]:
    validate_draft(draft, context)
    if draft.outcome != "draft":
        raise OperationError("plan_adoption_draft_required")
    original = context.plan.body
    assert isinstance(original, c.ContentPlan)
    topics = {slot.slot_index: slot.topic for slot in draft.slots}
    body = c.ContentPlan.model_validate(
        {
            **original.model_dump(),
            "slots": [
                slot.model_copy(update={"topic": topics[i]})
                for i, slot in enumerate(original.slots)
            ],
        }
    )
    notes = d.PlanNotesBody(
        fact_ids=context.fact_ids,
        evidence_record_ids=sorted(r.id for r in context.records),
        slots=sorted(draft.slots, key=lambda s: s.slot_index),
        warnings=draft.warnings,
        knowledge_gaps=draft.knowledge_gaps,
    )
    return body, notes


async def adoption_view(s: AsyncSession, wid: UUID, rid: UUID) -> d.PlanAdoptionView | None:
    row = await s.scalar(
        select(PlanAdoption).where(PlanAdoption.workspace_id == wid, PlanAdoption.run_id == rid)
    )
    return d.PlanAdoptionView.model_validate(row) if row else None


async def current_preview(
    s: AsyncSession, actor: Principal, wid: UUID, rid: UUID
) -> d.PlanAdoptionPreview:
    run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
    if run is None:
        raise OperationError("not_found", 404)
    if run.profile != "content_planner" or run.state != "needs_review":
        raise OperationError("plan_adoption_run_unavailable")
    if await adoption_view(s, wid, rid):
        raise OperationError("plan_already_adopted")
    await assert_registered_run(s, run)
    inputs, _, _, context = await current_input(s, run)
    artifact = await s.scalar(
        select(AIArtifact).where(AIArtifact.workspace_id == wid, AIArtifact.run_id == rid)
    )
    if (
        not isinstance(context, PlanningContext)
        or artifact is None
        or artifact.actor_id != actor.user_id
        or artifact.content_hash != c.canonical_hash(artifact.body)
    ):
        raise OperationError("plan_adoption_artifact_invalid")
    try:
        body, notes = candidate(PlanDraft.model_validate(artifact.body), context)
    except ValidationError:
        raise OperationError("plan_adoption_artifact_invalid") from None
    basis = {
        "run_id": rid,
        "artifact_id": artifact.id,
        "artifact_hash": artifact.content_hash,
        "input_id": inputs.id,
        "input_hash": inputs.content_hash,
        "source_plan_id": context.plan.id,
        "source_content_hash": context.plan.content_hash,
        "source_plan_number": context.plan.number,
        "expires_at": context.plan.expires_at.isoformat(),
        "proposed_content_hash": c.canonical_hash(body),
        "notes_hash": c.canonical_hash(notes),
    }
    preview_hash = c.canonical_hash(
        {
            "contract": "plan-adoption-v1",
            "workspace_id": str(wid),
            "actor_id": str(actor.user_id),
            **{
                key: str(value) if isinstance(value, UUID) else value
                for key, value in basis.items()
            },
        }
    )
    return d.PlanAdoptionPreview.model_validate(
        {**basis, "preview_hash": preview_hash, "body": body, "notes": notes}
    )


class PlanAdoptionService:
    def __init__(self, access: AccessService):
        self.access = access

    async def preview(
        self, actor: Principal, wid: UUID, rid: UUID, request: UUID
    ) -> d.PlanAdoptionPreview:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            return await current_preview(s, actor, wid, rid)

    async def read(
        self, actor: Principal, wid: UUID, rid: UUID, request: UUID
    ) -> d.PlanAdoptionView | None:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
            if run is None:
                raise OperationError("not_found", 404)
            if run.profile != "content_planner":
                raise OperationError("plan_adoption_run_unavailable")
            return await adoption_view(s, wid, rid)

    async def adopt(
        self, actor: Principal, wid: UUID, rid: UUID, command: d.AdoptPlanDraft, request: UUID
    ) -> d.PlanAdoptionView:
        if (
            command.human_confirmed is not True
            or command.share_with_workspace_confirmed is not True
        ):
            raise OperationError("plan_adoption_confirmation_required")
        safe_text(command.reason)
        fingerprint = c.canonical_hash(
            {"run_id": str(rid), **command.model_dump(mode="json", exclude={"idempotency_key"})}
        )
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            previous = await s.scalar(
                select(PlanAdoption).where(
                    PlanAdoption.workspace_id == wid,
                    PlanAdoption.actor_id == actor.user_id,
                    PlanAdoption.key_hash == digest(command.idempotency_key),
                )
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.PlanAdoptionView.model_validate(previous)
            preview = await current_preview(s, actor, wid, rid)
            if (
                command.artifact_id,
                command.artifact_hash,
                command.preview_hash,
                command.proposed_content_hash,
                command.notes_hash,
                command.expected_plan_number,
            ) != (
                preview.artifact_id,
                preview.artifact_hash,
                preview.preview_hash,
                preview.proposed_content_hash,
                preview.notes_hash,
                preview.source_plan_number,
            ):
                raise OperationError("plan_adoption_preview_changed")
            plan = await create_record(
                s,
                wid,
                actor.user_id,
                c.CreateRecord(
                    body=preview.body,
                    replaces_id=preview.source_plan_id,
                    expires_at=preview.expires_at,
                    idempotency_key=command.idempotency_key,
                ),
            )
            if plan.content_hash != preview.proposed_content_hash:
                raise OperationError("plan_adoption_content_changed")
            for target in preview.notes.evidence_record_ids:
                s.add(ContentLink(workspace_id=wid, record_id=plan.id, target_id=target))
            notes = PlanNotes(
                workspace_id=wid,
                plan_id=plan.id,
                plan_hash=plan.content_hash,
                content_hash=preview.notes_hash,
                actor_id=actor.user_id,
                body=preview.notes.model_dump(mode="json"),
            )
            s.add(notes)
            await s.flush()
            row = PlanAdoption(
                workspace_id=wid,
                actor_id=actor.user_id,
                run_id=rid,
                artifact_id=preview.artifact_id,
                artifact_hash=preview.artifact_hash,
                input_id=preview.input_id,
                input_hash=preview.input_hash,
                source_plan_id=preview.source_plan_id,
                source_content_hash=preview.source_content_hash,
                plan_id=plan.id,
                content_hash=plan.content_hash,
                plan_number=plan.number,
                notes_id=notes.id,
                notes_hash=notes.content_hash,
                preview_hash=preview.preview_hash,
                reason=command.reason,
                human_confirmed=True,
                share_with_workspace_confirmed=True,
                key_hash=digest(command.idempotency_key),
                request_hash=fingerprint,
            )
            s.add(row)
            await s.flush()
            audit(s, actor.user_id, wid, request, "content.plan_adopted", "draft", plan.id)
            return d.PlanAdoptionView.model_validate(row)
