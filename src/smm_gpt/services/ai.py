"""Personal commands enqueue durable runs; only the restricted worker calls a model."""

from uuid import UUID, uuid4

from sqlalchemy import func, select

from smm_gpt.core.config import Settings
from smm_gpt.domain import ai as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.copywriter import CopyDraft, CopywritingContext, RunCopyDraft
from smm_gpt.domain.editor import EditorContext, EditorialReview, RunEditorialReview
from smm_gpt.domain.knowledge import SearchRequest
from smm_gpt.domain.operations import OperationError, Page
from smm_gpt.infrastructure.ai_models import AIArtifact, AICancel, AIInput, AIRun
from smm_gpt.infrastructure.knowledge_models import RetrievalRun
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.ai_queue import current_input
from smm_gpt.services.copy_adoption import adoption_view
from smm_gpt.services.copywriter import validate_context, validate_draft
from smm_gpt.services.editor import snapshot, validate_review
from smm_gpt.services.editor_triage import triage_view
from smm_gpt.services.knowledge import brand_exists, eligible_citation, lock, retrieve
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.model_gateway import (
    assessment_payload,
    copywriting_payload,
    editorial_payload,
)
from smm_gpt.services.profiles import assert_registered_run, compatible_profile, selected_version


class AIService:
    def __init__(self, access: AccessService, settings: Settings):
        self.access, self.settings = access, settings

    async def profiles(self, actor: Principal, wid: UUID, request: UUID) -> list[d.Profile]:
        async with self.access.authorized(actor, wid, Permission.READ, request):
            return list(d.PROFILES)

    async def start(
        self,
        actor: Principal,
        wid: UUID,
        command: d.RunAssessment | RunEditorialReview | RunCopyDraft,
        request: UUID,
    ) -> d.AIRunView:
        question = (
            command.question
            if isinstance(command, d.RunAssessment)
            else command.direction
            if isinstance(command, RunCopyDraft)
            else "Review exact stored revision"
        )
        safe_text(question)
        profile = next(p for p in d.PROFILES if p.name == command.profile)
        # Nullable registry fields must not change pre-registry idempotency identities.
        fingerprint = canonical_hash(
            command.model_dump(mode="json", exclude={"idempotency_key"}, exclude_none=True)
        )
        existing_id: UUID | None = None
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            await brand_exists(s, wid, command.brand_id)
            existing = await s.scalar(
                select(AIRun).where(
                    AIRun.workspace_id == wid,
                    AIRun.actor_id == actor.user_id,
                    AIRun.key_hash == digest(command.idempotency_key),
                )
            )
            if existing:
                if existing.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                existing_id = existing.id
            else:
                count = await s.scalar(select(func.smm_ai_recent_count(wid)))
                if (count or 0) >= self.settings.ai_daily_run_limit:
                    raise OperationError("ai_run_quota_exceeded", 429)
                error = profile.blocked_reason
                if command.profile == "editor" and isinstance(command, d.RunAssessment):
                    error = "editor_revision_request_required"
                if command.profile == "copywriter" and isinstance(command, d.RunAssessment):
                    error = "copywriter_revision_request_required"
                selected = None
                if not error and (
                    self.settings.ai_provider == "disabled"
                    or wid not in self.settings.ai_allowed_workspaces
                ):
                    error = "model_provider_disabled"
                if not error:
                    try:
                        selected = await selected_version(
                            s,
                            wid,
                            command.profile,
                            command.profile_version_id,
                            command.profile_selection_id,
                        )
                        profile = compatible_profile(selected)
                        if (selected.provider, selected.model) != (
                            self.settings.ai_provider,
                            self.settings.ai_model,
                        ):
                            raise OperationError("profile_model_changed")
                    except OperationError as exc:
                        error = exc.code
                citations = []
                context = None
                copy_context = None
                if not error and isinstance(command, (RunEditorialReview, RunCopyDraft)):
                    try:
                        context = await snapshot(
                            s,
                            wid,
                            command.brand_id,
                            command.post_id,
                            command.revision_id,
                            command.content_hash,
                        )
                        if isinstance(command, RunCopyDraft):
                            copy_context = CopywritingContext(
                                source=context, direction=command.direction
                            )
                            validate_context(copy_context)
                    except OperationError as exc:
                        error = exc.code
                if not error and isinstance(command, d.RunAssessment):
                    citations = await retrieve(
                        s,
                        wid,
                        SearchRequest(query=question, brand_id=command.brand_id),
                        at=utcnow(),
                    )
                    if not citations:
                        error = "knowledge_gap_no_current_sources"
                run = AIRun(
                    id=uuid4(),
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    identity_id=actor.identity_id,
                    brand_id=command.brand_id,
                    key_hash=digest(command.idempotency_key),
                    request_hash=fingerprint,
                    profile=profile.name,
                    profile_version=profile.version,
                    profile_snapshot=profile.model_dump(mode="json"),
                    profile_version_id=selected.id if selected else None,
                    profile_selection_id=command.profile_selection_id if selected else None,
                    state="blocked" if error else "queued",
                    error_code=error,
                    provider=self.settings.ai_provider,
                    model=self.settings.ai_model,
                    usage={
                        "cost_usd": None,
                        "cost_status": "not_called",
                        "max_output_tokens": 2000,
                        "attempts": 0,
                    },
                    finished_at=utcnow() if error else None,
                )
                if not error and citations:
                    trace = RetrievalRun(
                        id=uuid4(),
                        workspace_id=wid,
                        actor_id=actor.user_id,
                        brand_id=command.brand_id,
                        query_hash=digest(question),
                        algorithm="ru-simple-v1",
                        chunk_ids=[str(c.chunk_id) for c in citations],
                    )
                    s.add(trace)
                    await s.flush()
                    run.retrieval_run_id = trace.id
                s.add(run)
                await s.flush()
                if not error:
                    payload = (
                        copywriting_payload(
                            profile, copy_context.model_dump(mode="json"), self.settings.ai_model
                        )
                        if copy_context
                        else editorial_payload(
                            profile, context.model_dump(mode="json"), self.settings.ai_model
                        )
                        if context
                        else assessment_payload(
                            profile, question, citations, self.settings.ai_model
                        )
                    )
                    safe_text(str(payload))
                    s.add(
                        AIInput(
                            workspace_id=wid,
                            actor_id=actor.user_id,
                            run_id=run.id,
                            question=question,
                            citations=[c.model_dump(mode="json") for c in citations],
                            payload=payload,
                            content_hash=canonical_hash(payload),
                            post_id=context.post_id if context else None,
                            revision_id=context.revision.id if context else None,
                            editor_context=context.model_dump(mode="json")
                            if context and not copy_context
                            else None,
                            copy_context=copy_context.model_dump(mode="json")
                            if copy_context
                            else None,
                        )
                    )
                audit(s, actor.user_id, wid, request, "ai.run_reserved", run.state, run.id)
                return d.AIRunView.model_validate(run)
        assert existing_id
        # Includes terminal/unknown runs: the same key NEVER starts another provider call.
        return await self.read(actor, wid, existing_id, request)

    async def cancel(
        self, actor: Principal, wid: UUID, rid: UUID, command: d.CancelAssessment, request: UUID
    ) -> d.AICancelReceipt:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            fingerprint = canonical_hash({"run_id": str(rid), "version": command.expected_version})
            previous = await s.scalar(
                select(AICancel).where(
                    AICancel.workspace_id == wid,
                    AICancel.actor_id == actor.user_id,
                    AICancel.key_hash == digest(command.idempotency_key),
                )
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.AICancelReceipt.model_validate(previous.result)
            run = await s.scalar(
                select(AIRun)
                .where(
                    AIRun.workspace_id == wid,
                    AIRun.id == rid,
                )
                .with_for_update()
            )
            if not run:
                raise OperationError("not_found", 404)
            if run.version != command.expected_version:
                raise OperationError("run_conflict")
            if run.state not in {"queued", "running", "cancel_requested"}:
                raise OperationError("run_cancel_not_allowed")
            if run.state != "cancel_requested":
                run.state = "cancelled" if run.state == "queued" else "cancel_requested"
                run.version += 1
                if run.state == "cancelled":
                    run.finished_at = utcnow()
            receipt = d.AICancelReceipt(run_id=rid, state=run.state, version=run.version)
            s.add(
                AICancel(
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    run_id=rid,
                    key_hash=digest(command.idempotency_key),
                    request_hash=fingerprint,
                    result=receipt.model_dump(mode="json"),
                )
            )
            audit(s, actor.user_id, wid, request, "ai.cancel_requested", run.state, rid)
            return receipt

    async def inputs(self, actor: Principal, wid: UUID, rid: UUID, request: UUID) -> d.AIInputView:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
            if not run:
                raise OperationError("not_found", 404)
            await lock(s, wid)
            record, _, citations, context = await current_input(s, run, require_latest=False)
            return d.AIInputView(
                run_id=rid,
                content_hash=record.content_hash,
                question=record.question,
                citations=citations,
                payload=record.payload,
                editor_context=context if isinstance(context, EditorContext) else None,
                copy_context=context if isinstance(context, CopywritingContext) else None,
            )

    async def read(self, actor: Principal, wid: UUID, rid: UUID, request: UUID) -> d.AIRunView:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
            if run is None:
                raise OperationError("not_found", 404)
            view = d.AIRunView.model_validate(run)
            if run.profile == "copywriter":
                view.copy_adoption = await adoption_view(s, wid, rid)
            artifact = await s.scalar(
                select(AIArtifact).where(
                    AIArtifact.workspace_id == wid,
                    AIArtifact.run_id == rid,
                )
            )
            if artifact and run.state == "needs_review":
                try:
                    await lock(s, wid)
                    await assert_registered_run(s, run)
                    if run.profile == "copywriter":
                        _, _, _, context = await current_input(s, run)
                        if not isinstance(context, CopywritingContext):
                            raise OperationError("copywriter_input_invalid")
                        draft = CopyDraft.model_validate(artifact.body)
                        if canonical_hash(artifact.body) != artifact.content_hash:
                            raise OperationError("copywriter_artifact_invalid")
                        validate_draft(draft, context)
                        view.copy_draft = draft
                        return view
                    if run.profile == "editor":
                        _, _, _, context = await current_input(s, run)
                        if not isinstance(context, EditorContext):
                            raise OperationError("editor_input_invalid")
                        review = EditorialReview.model_validate(artifact.body)
                        if canonical_hash(artifact.body) != artifact.content_hash:
                            raise OperationError("editor_report_invalid")
                        validate_review(review, context)
                        view.editorial_review = review
                        view.editorial_triage = await triage_view(s, artifact, review)
                        return view
                    view.citations = [
                        await eligible_citation(s, wid, UUID(cid), run.brand_id)
                        for cid in artifact.citation_ids
                    ]
                    view.assessment = d.ReferenceAssessment.model_validate(artifact.body)
                except OperationError as exc:
                    view.citations = []
                    view.error_code = (
                        "artifact_profile_stale_or_unavailable"
                        if exc.code.startswith("profile_")
                        else "artifact_copywriter_stale_or_unavailable"
                        if run.profile == "copywriter"
                        else "artifact_editor_stale_or_unavailable"
                        if run.profile == "editor"
                        else "artifact_sources_stale_or_unavailable"
                    )
            return view

    async def runs(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.AIRunView]:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            query = select(AIRun).where(AIRun.workspace_id == wid)
            if cursor:
                query = query.where(AIRun.id > cursor)
            rows = list((await s.scalars(query.order_by(AIRun.id).limit(limit + 1))).all())
            return Page(
                items=[d.AIRunView.model_validate(r) for r in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )
