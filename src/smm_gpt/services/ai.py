"""Testing runs reserve quota before I/O; no AI path receives a human service principal."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select

from smm_gpt.core.config import Settings
from smm_gpt.domain import ai as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.knowledge import SearchRequest
from smm_gpt.domain.operations import OperationError, Page
from smm_gpt.infrastructure.ai_models import AIArtifact, AIRun
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.knowledge import KnowledgeService, brand_exists, eligible_citation, lock
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.model_gateway import OpenAITextGateway, TextGateway


class AIService:
    def __init__(
        self, access: AccessService, settings: Settings, gateway: TextGateway | None = None
    ):
        self.access, self.settings = access, settings
        self.gateway = gateway or OpenAITextGateway(settings)

    async def profiles(self, actor: Principal, wid: UUID, request: UUID) -> list[d.Profile]:
        async with self.access.authorized(actor, wid, Permission.READ, request):
            return list(d.PROFILES)

    async def start(
        self, actor: Principal, wid: UUID, command: d.RunAssessment, request: UUID
    ) -> d.AIRunView:
        safe_text(command.question)
        profile = next(p for p in d.PROFILES if p.name == command.profile)
        fingerprint = canonical_hash(command.model_dump(mode="json", exclude={"idempotency_key"}))
        existing_id: UUID | None = None
        run: AIRun | None
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
                if not error and (
                    self.settings.ai_provider == "disabled"
                    or wid not in self.settings.ai_allowed_workspaces
                ):
                    error = "model_provider_disabled"
                run = AIRun(
                    id=uuid4(),
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    brand_id=command.brand_id,
                    key_hash=digest(command.idempotency_key),
                    request_hash=fingerprint,
                    profile=profile.name,
                    profile_version=profile.version,
                    profile_snapshot=profile.model_dump(mode="json"),
                    state="blocked" if error else "running",
                    error_code=error,
                    provider=self.settings.ai_provider,
                    model=self.settings.ai_model,
                    usage={
                        "cost_usd": None,
                        "cost_status": "not_called" if error else "unknown",
                        "max_output_tokens": 2000,
                        "attempts": 0,
                    },
                )
                s.add(run)
                audit(s, actor.user_id, wid, request, "ai.run_reserved", run.state, run.id)
                run_id, blocked = run.id, error is not None
        if existing_id:
            # Includes interrupted/unknown outcomes: never issue a second provider call.
            return await self.read(actor, wid, existing_id, request)
        if blocked:
            return await self.read(actor, wid, run_id, request)
        try:
            result = await KnowledgeService(self.access).search(
                actor,
                wid,
                SearchRequest(query=command.question, brand_id=command.brand_id),
                request,
            )
            if not result.citations:
                raise OperationError("knowledge_gap_no_current_sources")
            # Recheck sources and authorization immediately before the external boundary.
            async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
                run = await s.get(AIRun, run_id)
                if run is None:
                    raise OperationError("not_found", 404)
                for c in result.citations:
                    await eligible_citation(s, wid, c.chunk_id, command.brand_id)
                run.retrieval_run_id = result.run_id
                run.usage = {**run.usage, "attempts": 1}
            generated = await self.gateway.assess(profile, command.question, result.citations)
            # The injected gateway used by tests is not trusted more than the real adapter.
            body = d.ReferenceAssessment.model_validate(generated.assessment.model_dump())
            safe_text(body.model_dump_json())
            used = {cid for statement in body.statements for cid in statement.citation_ids}
            if not used <= {c.chunk_id for c in result.citations}:
                raise OperationError("model_citation_invalid")
            async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
                await lock(s, wid)
                run = await s.get(AIRun, run_id)
                if run is None or run.state != "running":
                    raise OperationError("run_conflict")
                # ALL context is rechecked: even a hypothesis can derive from a stale source.
                for c in result.citations:
                    await eligible_citation(s, wid, c.chunk_id, command.brand_id)
                run.state = "needs_review"
                run.model = generated.model
                run.usage = {
                    "input_tokens": generated.input_tokens,
                    "output_tokens": generated.output_tokens,
                    "response_id": generated.response_id,
                    "attempts": 1,
                    "max_output_tokens": 2000,
                    "cost_usd": None,
                    "cost_status": "provider_invoice_required",
                }
                s.add(
                    AIArtifact(
                        workspace_id=wid,
                        actor_id=actor.user_id,
                        run_id=run_id,
                        body=body.model_dump(mode="json"),
                        content_hash=canonical_hash(body.model_dump(mode="json")),
                        citation_ids=[str(c.chunk_id) for c in result.citations],
                    )
                )
                audit(
                    s, actor.user_id, wid, request, "ai.artifact_proposed", "needs_review", run_id
                )
        except OperationError as exc:
            async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
                run = await s.get(AIRun, run_id)
                if run:
                    run.state = "unknown" if exc.code == "model_outcome_unknown" else "failed"
                    run.error_code = exc.code
                    audit(s, actor.user_id, wid, request, "ai.run_failed", run.state, run_id)
        return await self.read(actor, wid, run_id, request)

    async def read(self, actor: Principal, wid: UUID, rid: UUID, request: UUID) -> d.AIRunView:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            run = await s.scalar(select(AIRun).where(AIRun.workspace_id == wid, AIRun.id == rid))
            if run is None:
                raise OperationError("not_found", 404)
            view = d.AIRunView.model_validate(run)
            if run.state == "running" and run.created_at < utcnow() - timedelta(minutes=2):
                view.state, view.error_code = "unknown", "interrupted_run_not_replayed"
            artifact = await s.scalar(
                select(AIArtifact).where(AIArtifact.workspace_id == wid, AIArtifact.run_id == rid)
            )
            if artifact:
                try:
                    view.citations = [
                        await eligible_citation(s, wid, UUID(cid), run.brand_id)
                        for cid in artifact.citation_ids
                    ]
                    view.assessment = d.ReferenceAssessment.model_validate(artifact.body)
                except OperationError:
                    view.citations = []
                    view.error_code = "artifact_sources_stale_or_unavailable"
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
            # List never includes text artifacts or sources; detail rechecks current access.
            return Page(
                items=[d.AIRunView.model_validate(r) for r in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )
