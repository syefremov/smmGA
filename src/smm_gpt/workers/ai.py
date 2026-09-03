"""At-most-one dispatch reservation. Lost/uncertain executions are NEVER requeued."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text

from smm_gpt.core.config import Settings, get_settings
from smm_gpt.domain.ai import ReferenceAssessment
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.copywriter import CopyDraft, CopywritingContext
from smm_gpt.domain.editor import EditorContext, EditorialReview
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIArtifact, AIRun
from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import audit
from smm_gpt.services.ai_queue import authorized, current_input, executable
from smm_gpt.services.copywriter import validate_draft
from smm_gpt.services.editor import validate_review
from smm_gpt.services.knowledge import lock
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.model_gateway import (
    CopywritingGateway,
    CopywritingGatewayResult,
    EditorialGateway,
    EditorialGatewayResult,
    GatewayResult,
    OpenAITextGateway,
    TextGateway,
)
from smm_gpt.services.profiles import assert_registered_run


async def process(
    database: Database,
    settings: Settings,
    gateway: TextGateway,
    wid: UUID,
    rid: UUID,
    actor: UUID,
    *,
    editorial_gateway: EditorialGateway | None = None,
    copywriting_gateway: CopywritingGateway | None = None,
) -> bool:
    token = uuid4()
    async with database.transaction(actor, wid) as s:
        await lock(s, wid)
        run = await s.scalar(
            select(AIRun)
            .where(
                AIRun.workspace_id == wid,
                AIRun.id == rid,
                AIRun.actor_id == actor,
            )
            .with_for_update(skip_locked=True)
        )
        if run is None or run.state != "queued":
            return False
        try:
            if not settings.ai_worker_enabled:
                return False
            if not await authorized(s, wid, actor, run.identity_id):
                raise OperationError("authorization_changed")
            if run.created_at < utcnow() - timedelta(hours=24):
                raise OperationError("queue_expired")
            record, profile, citations, context = await current_input(s, run)
            await assert_registered_run(s, run)
            executable(settings, run, record, profile, citations, context)
            question = record.question
        except OperationError as exc:
            run.state, run.error_code = "blocked", exc.code
            run.version += 1
            run.finished_at = utcnow()
            audit(s, actor, wid, uuid4(), "ai.dispatch_blocked", "blocked", rid)
            return False
        run.state, run.lease_id = "running", token
        run.started_at = utcnow()
        run.lease_until = utcnow() + timedelta(seconds=120)
        run.version += 1
        run.usage = {**run.usage, "attempts": 1, "cost_status": "unknown"}
        audit(s, actor, wid, uuid4(), "ai.dispatch_reserved", "running", rid)
    # Durable dispatch reservation commits BEFORE network I/O. A crash may lose work, not replay it.
    generated: GatewayResult | EditorialGatewayResult | CopywritingGatewayResult | None = None
    body: ReferenceAssessment | EditorialReview | CopyDraft | None = None
    error: str | None = None
    unknown = False
    try:
        async with asyncio.timeout(60):
            candidate: GatewayResult | EditorialGatewayResult | CopywritingGatewayResult
            if isinstance(context, CopywritingContext):
                drafted = await (copywriting_gateway or OpenAITextGateway(settings)).draft(
                    profile, context
                )
                candidate = CopywritingGatewayResult.model_validate(drafted.model_dump())
            elif context:
                reviewed = await (editorial_gateway or OpenAITextGateway(settings)).review(
                    profile, context
                )
                candidate = EditorialGatewayResult.model_validate(reviewed.model_dump())
            else:
                returned = await gateway.assess(profile, question, citations)
                candidate = GatewayResult.model_validate(returned.model_dump())
        safe_text(candidate.model)
        safe_text(candidate.response_id)
        generated = candidate
        if isinstance(generated, CopywritingGatewayResult):
            assert isinstance(context, CopywritingContext)
            body = CopyDraft.model_validate(generated.draft.model_dump())
            validate_draft(body, context)
        elif isinstance(generated, EditorialGatewayResult):
            assert isinstance(context, EditorContext)
            body = EditorialReview.model_validate(generated.review.model_dump())
            validate_review(body, context)
        else:
            body = ReferenceAssessment.model_validate(generated.assessment.model_dump())
            used = {cid for statement in body.statements for cid in statement.citation_ids}
            if not used <= {c.chunk_id for c in citations}:
                raise OperationError("model_citation_invalid")
        safe_text(body.model_dump_json())
    except OperationError as exc:
        error, unknown = exc.code, exc.code == "model_outcome_unknown"
    except Exception:
        error, unknown = "model_outcome_unknown", True
    async with database.transaction(actor, wid) as s:
        await lock(s, wid)
        run = await s.scalar(
            select(AIRun)
            .where(
                AIRun.workspace_id == wid,
                AIRun.id == rid,
            )
            .with_for_update()
        )
        if (
            run is None
            or run.lease_id != token
            or run.state not in {"running", "cancel_requested"}
            or not run.lease_until
            or run.lease_until <= utcnow()
        ):
            return False
        if not await authorized(s, wid, actor, run.identity_id):
            error = "authorization_changed"
        else:
            try:
                record, profile, current, context = await current_input(s, run)
                await assert_registered_run(s, run)
                executable(settings, run, record, profile, current, context)
            except OperationError as exc:
                error = exc.code
        if body and not error and run.state == "running":
            s.add(
                AIArtifact(
                    workspace_id=wid,
                    actor_id=actor,
                    run_id=rid,
                    body=body.model_dump(mode="json"),
                    content_hash=canonical_hash(body.model_dump(mode="json")),
                    citation_ids=[str(c.chunk_id) for c in citations],
                )
            )
            # Database transition guard requires the matching immutable artifact first.
            await s.flush()
        if generated:
            run.usage = {
                "input_tokens": generated.input_tokens,
                "output_tokens": generated.output_tokens,
                "response_id": generated.response_id,
                "attempts": 1,
                "max_output_tokens": 2000,
                "cost_usd": None,
                "cost_status": "provider_invoice_required",
            }
            run.model = generated.model
        if run.state == "cancel_requested":
            run.state = "unknown" if unknown else "cancelled"
            run.error_code = error
        elif error:
            run.state, run.error_code = ("unknown" if unknown else "failed"), error
        elif body:
            run.state = "needs_review"
        run.version += 1
        run.finished_at, run.lease_until = utcnow(), None
        audit(s, actor, wid, uuid4(), "ai.run_finished", run.state, rid)
        succeeded = bool(run.state == "needs_review")
    return succeeded


async def reconcile(database: Database) -> int:
    # Restricted SECURITY DEFINER function: bounded stale/unauthorized state changes + audit only.
    async with database.transaction() as s:
        return int(await s.scalar(text("SELECT public.smm_ai_reconcile()")) or 0)


async def poll(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not settings.ai_worker_enabled:
        return 0
    database = Database(settings.database_url.get_secret_value(), 5)
    try:
        await database.require_restricted_role()
        await reconcile(database)
        async with database.transaction() as s:
            pending = (await s.execute(text("SELECT * FROM public.smm_ai_pending()"))).all()
        count = 0
        for row in pending:
            count += await process(
                database,
                settings,
                OpenAITextGateway(settings),
                row.workspace_id,
                row.run_id,
                row.actor_id,
            )
        return count
    finally:
        await database.close()
