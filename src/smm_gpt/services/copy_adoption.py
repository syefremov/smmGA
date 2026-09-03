"""Human-only adoption. Never passed to a model or worker, never an approval operation."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import content as c
from smm_gpt.domain import copy_adoption as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.copywriter import CopyDraft, CopywritingContext
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIArtifact, AIRun, CopyAdoption
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.ai_queue import current_input
from smm_gpt.services.content import post_row
from smm_gpt.services.content_preflight import preflight
from smm_gpt.services.content_revision import save_revision
from smm_gpt.services.copywriter import validate_draft
from smm_gpt.services.knowledge import lock
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.profiles import assert_registered_run


def candidate_body(draft: CopyDraft, context: CopywritingContext) -> c.RevisionBody:
    validate_draft(draft, context)
    if draft.outcome != "draft":
        raise OperationError("copy_adoption_draft_required")
    original = context.source.revision.body
    used = {e.fact_id for v in draft.variants for e in v.evidence}
    if not used <= set(original.fact_ids):
        raise OperationError("copy_adoption_evidence_unmapped")
    values = {v.variant_index: v.text for v in draft.variants}
    try:
        return c.RevisionBody(
            variants=[
                v.model_copy(update={"text": values[i]}) for i, v in enumerate(original.variants)
            ],
            fact_ids=original.fact_ids,
            knowledge_gaps=draft.knowledge_gaps,
        )
    except ValidationError:
        # CopyDraft can contain 30 gaps, a content revision only 20. Never silently truncate.
        raise OperationError("copy_adoption_content_limits_exceeded") from None


async def adoption_view(s: AsyncSession, wid: UUID, rid: UUID) -> d.CopyAdoptionView | None:
    row = await s.scalar(
        select(CopyAdoption).where(
            CopyAdoption.workspace_id == wid,
            CopyAdoption.run_id == rid,
        )
    )
    return d.CopyAdoptionView.model_validate(row) if row else None


async def current_preview(
    s: AsyncSession, actor: Principal, wid: UUID, rid: UUID
) -> d.CopyAdoptionPreview:
    # Caller owns knowledge lock. current_input acquires content lock before reading SQL state.
    run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
    if run is None:
        raise OperationError("not_found", 404)
    if run.profile != "copywriter" or run.state != "needs_review":
        raise OperationError("copy_adoption_run_unavailable")
    if await adoption_view(s, wid, rid):
        raise OperationError("copy_already_adopted")
    await assert_registered_run(s, run)
    inputs, _, _, context = await current_input(s, run)
    artifact = await s.scalar(
        select(AIArtifact).where(
            AIArtifact.workspace_id == wid,
            AIArtifact.run_id == rid,
        )
    )
    if (
        not isinstance(context, CopywritingContext)
        or artifact is None
        or artifact.actor_id != actor.user_id
        or artifact.content_hash != c.canonical_hash(artifact.body)
    ):
        raise OperationError("copy_adoption_artifact_invalid")
    try:
        draft = CopyDraft.model_validate(artifact.body)
    except ValidationError:
        raise OperationError("copy_adoption_artifact_invalid") from None
    body = candidate_body(draft, context)
    post = await post_row(s, wid, context.source.post_id)
    proposed_hash = c.canonical_hash({"body": body.model_dump(mode="json"), "media_manifest": []})
    basis = {
        "run_id": rid,
        "artifact_id": artifact.id,
        "artifact_hash": artifact.content_hash,
        "input_id": inputs.id,
        "input_hash": inputs.content_hash,
        "post_id": post.id,
        "post_version": post.version,
        "source_revision_id": context.source.revision.id,
        "source_content_hash": context.source.revision.content_hash,
        "proposed_content_hash": proposed_hash,
    }
    preview_hash = c.canonical_hash(
        {
            "contract": "copy-adoption-v1",
            "workspace_id": str(wid),
            "actor_id": str(actor.user_id),
            **{
                key: str(value) if isinstance(value, UUID) else value
                for key, value in basis.items()
            },
        }
    )
    return d.CopyAdoptionPreview.model_validate(
        {
            **basis,
            "preview_hash": preview_hash,
            "body": body,
            "draft": draft,
        }
    )


class CopyAdoptionService:
    def __init__(self, access: AccessService):
        self.access = access

    async def preview(
        self, actor: Principal, wid: UUID, rid: UUID, request: UUID
    ) -> d.CopyAdoptionPreview:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            return await current_preview(s, actor, wid, rid)

    async def read(
        self, actor: Principal, wid: UUID, rid: UUID, request: UUID
    ) -> d.CopyAdoptionView | None:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
            if run is None:
                raise OperationError("not_found", 404)
            if run.profile != "copywriter":
                raise OperationError("copy_adoption_run_unavailable")
            # Receipt survives later edits/expiry/profile changes. It does not expose source text.
            return await adoption_view(s, wid, rid)

    async def adopt(
        self,
        actor: Principal,
        wid: UUID,
        rid: UUID,
        command: d.AdoptCopyDraft,
        request: UUID,
    ) -> d.CopyAdoptionView:
        if (
            command.human_confirmed is not True
            or command.share_with_workspace_confirmed is not True
        ):
            raise OperationError("copy_adoption_confirmation_required")
        safe_text(command.reason)
        fingerprint = c.canonical_hash(
            {
                "run_id": str(rid),
                **command.model_dump(mode="json", exclude={"idempotency_key"}),
            }
        )
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            previous = await s.scalar(
                select(CopyAdoption).where(
                    CopyAdoption.workspace_id == wid,
                    CopyAdoption.actor_id == actor.user_id,
                    CopyAdoption.key_hash == digest(command.idempotency_key),
                )
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.CopyAdoptionView.model_validate(previous)
            preview = await current_preview(s, actor, wid, rid)
            if (
                command.artifact_id,
                command.artifact_hash,
                command.preview_hash,
                command.proposed_content_hash,
                command.expected_post_version,
            ) != (
                preview.artifact_id,
                preview.artifact_hash,
                preview.preview_hash,
                preview.proposed_content_hash,
                preview.post_version,
            ):
                raise OperationError("copy_adoption_preview_changed")
            post = await post_row(s, wid, preview.post_id)
            revision = await save_revision(s, post, actor.user_id, preview.body)
            if revision.content_hash != preview.proposed_content_hash:
                raise OperationError("copy_adoption_content_changed")
            checks = await preflight(s, post, revision)
            row = CopyAdoption(
                workspace_id=wid,
                actor_id=actor.user_id,
                run_id=rid,
                artifact_id=preview.artifact_id,
                artifact_hash=preview.artifact_hash,
                input_id=preview.input_id,
                input_hash=preview.input_hash,
                post_id=post.id,
                source_revision_id=preview.source_revision_id,
                source_content_hash=preview.source_content_hash,
                revision_id=revision.id,
                content_hash=revision.content_hash,
                post_version=post.version,
                preview_hash=preview.preview_hash,
                reason=command.reason,
                preflight=checks.model_dump(mode="json"),
                human_confirmed=True,
                share_with_workspace_confirmed=True,
                key_hash=digest(command.idempotency_key),
                request_hash=fingerprint,
            )
            # Revision + approval invalidation + provenance + audit commit atomically.
            s.add(row)
            await s.flush()
            audit(s, actor.user_id, wid, request, "content.copy_adopted", "draft", revision.id)
            return d.CopyAdoptionView.model_validate(row)
