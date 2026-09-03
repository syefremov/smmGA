"""Exact, owner-confirmed AI finding triage, independent from publication approval."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import editor_triage as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.editor import EditorialReview
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIArtifact, AIRun, EditorialDecision
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.ai_queue import current_input
from smm_gpt.services.editor import validate_review
from smm_gpt.services.knowledge import lock
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.profiles import assert_registered_run


async def current_report(
    s: AsyncSession, wid: UUID, rid: UUID
) -> tuple[AIArtifact, EditorialReview]:
    # Caller holds knowledge lock; current_input takes content lock, never the reverse.
    run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
    if run is None:
        raise OperationError("not_found", 404)
    if run.profile != "editor" or run.state != "needs_review":
        raise OperationError("editor_report_unavailable")
    await assert_registered_run(s, run)
    _, _, _, context = await current_input(s, run)
    artifact = await s.scalar(
        select(AIArtifact).where(AIArtifact.workspace_id == wid, AIArtifact.run_id == rid)
    )
    if (
        context is None
        or artifact is None
        or canonical_hash(artifact.body) != artifact.content_hash
    ):
        raise OperationError("editor_report_invalid")
    review = EditorialReview.model_validate(artifact.body)
    validate_review(review, context)
    return artifact, review


async def history_page(
    s: AsyncSession, wid: UUID, rid: UUID, before: int | None = None
) -> d.EditorialHistory:
    query = select(EditorialDecision).where(
        EditorialDecision.workspace_id == wid, EditorialDecision.run_id == rid
    )
    if before is not None:
        query = query.where(EditorialDecision.sequence < before)
    rows = list(
        (await s.scalars(query.order_by(EditorialDecision.sequence.desc()).limit(26))).all()
    )
    return d.EditorialHistory(
        items=[d.EditorialDecisionView.model_validate(row) for row in rows[:25]],
        next_before=rows[24].sequence if len(rows) > 25 else None,
    )


async def triage_view(
    s: AsyncSession, artifact: AIArtifact, review: EditorialReview
) -> d.EditorialTriageView:
    latest = (
        await s.scalars(
            select(EditorialDecision)
            .where(
                EditorialDecision.workspace_id == artifact.workspace_id,
                EditorialDecision.run_id == artifact.run_id,
            )
            .distinct(EditorialDecision.finding_index)
            .order_by(EditorialDecision.finding_index, EditorialDecision.sequence.desc())
        )
    ).all()
    decisions = {row.finding_index: d.EditorialDecisionView.model_validate(row) for row in latest}
    history = await history_page(s, artifact.workspace_id, artifact.run_id)
    return d.EditorialTriageView(
        run_id=artifact.run_id,
        artifact_id=artifact.id,
        artifact_hash=artifact.content_hash,
        revision_id=review.revision_id,
        content_hash=review.content_hash,
        version=max((row.sequence for row in latest), default=0),
        findings=[
            d.EditorialFindingState(
                finding_index=index,
                finding_hash=canonical_hash(finding),
                status=decisions[index].status if index in decisions else "open",
                latest_decision=decisions.get(index),
            )
            for index, finding in enumerate(review.findings)
        ],
        recent_history=history.items,
        next_before=history.next_before,
    )


class EditorTriageService:
    def __init__(self, access: AccessService):
        self.access = access

    async def read(
        self, actor: Principal, wid: UUID, rid: UUID, request: UUID
    ) -> d.EditorialTriageView:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            artifact, review = await current_report(s, wid, rid)
            return await triage_view(s, artifact, review)

    async def history(
        self, actor: Principal, wid: UUID, rid: UUID, request: UUID, before: int | None = None
    ) -> d.EditorialHistory:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
            if run is None:
                raise OperationError("not_found", 404)
            if run.profile != "editor":
                raise OperationError("editor_report_unavailable")
            # Historical rationale only, no source text/model output or claim of current validity.
            return await history_page(s, wid, rid, before)

    async def decide(
        self,
        actor: Principal,
        wid: UUID,
        rid: UUID,
        command: d.DecideEditorialFinding,
        request: UUID,
    ) -> d.EditorialDecisionReceipt:
        safe_text(command.reason)
        fingerprint = canonical_hash(
            {"run_id": str(rid), **command.model_dump(mode="json", exclude={"idempotency_key"})}
        )
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            previous = await s.scalar(
                select(EditorialDecision).where(
                    EditorialDecision.workspace_id == wid,
                    EditorialDecision.actor_id == actor.user_id,
                    EditorialDecision.key_hash == digest(command.idempotency_key),
                )
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.EditorialDecisionReceipt(
                    decision=d.EditorialDecisionView.model_validate(previous)
                )
            artifact, review = await current_report(s, wid, rid)
            if (
                command.artifact_id,
                command.artifact_hash,
                command.revision_id,
                command.content_hash,
            ) != (
                artifact.id,
                artifact.content_hash,
                review.revision_id,
                review.content_hash,
            ):
                raise OperationError("editor_triage_binding_invalid")
            if command.finding_index >= len(
                review.findings
            ) or command.finding_hash != canonical_hash(review.findings[command.finding_index]):
                raise OperationError("editor_finding_changed")
            version = (
                await s.scalar(
                    select(func.max(EditorialDecision.sequence)).where(
                        EditorialDecision.workspace_id == wid,
                        EditorialDecision.run_id == rid,
                    )
                )
                or 0
            )
            if version != command.expected_version:
                raise OperationError("editor_triage_version_conflict")
            last = await s.scalar(
                select(EditorialDecision)
                .where(
                    EditorialDecision.workspace_id == wid,
                    EditorialDecision.run_id == rid,
                    EditorialDecision.finding_index == command.finding_index,
                )
                .order_by(EditorialDecision.sequence.desc())
                .limit(1)
            )
            if command.status == (last.status if last else "open"):
                raise OperationError("editor_finding_state_unchanged")
            row = EditorialDecision(
                workspace_id=wid,
                actor_id=actor.user_id,
                run_id=rid,
                artifact_id=artifact.id,
                artifact_hash=artifact.content_hash,
                revision_id=review.revision_id,
                content_hash=review.content_hash,
                finding_index=command.finding_index,
                finding_hash=command.finding_hash,
                sequence=version + 1,
                status=command.status,
                reason=command.reason,
                key_hash=digest(command.idempotency_key),
                request_hash=fingerprint,
            )
            s.add(row)
            await s.flush()
            audit(s, actor.user_id, wid, request, "ai.finding_decided", command.status, row.id)
            return d.EditorialDecisionReceipt(decision=d.EditorialDecisionView.model_validate(row))
