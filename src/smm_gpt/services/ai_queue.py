"""Shared queue invariants. No human principal, external I/O or state mutations."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.core.config import Settings
from smm_gpt.domain.ai import PROFILES, Profile
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.copywriter import CopywritingContext
from smm_gpt.domain.editor import EditorContext
from smm_gpt.domain.knowledge import Citation
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.planner import PlanningContext
from smm_gpt.infrastructure.ai_models import AIInput, AIRun
from smm_gpt.infrastructure.models import Identity, Membership, User
from smm_gpt.services.copywriter import validate_context
from smm_gpt.services.editor import snapshot
from smm_gpt.services.knowledge import eligible_citation
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.model_gateway import (
    assessment_payload,
    copywriting_payload,
    editorial_payload,
    planning_payload,
)
from smm_gpt.services.planner import snapshot as planning_snapshot
from smm_gpt.services.planner import validate_context as validate_planning_context


async def authorized(s: AsyncSession, wid: UUID, actor: UUID, identity: UUID | None) -> bool:
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
                Membership.role == "owner",
            )
        )
    )


async def current_input(
    s: AsyncSession,
    run: AIRun,
    *,
    require_latest: bool = True,
) -> tuple[
    AIInput, Profile, list[Citation], EditorContext | CopywritingContext | PlanningContext | None
]:
    record = await s.scalar(
        select(AIInput).where(
            AIInput.workspace_id == run.workspace_id,
            AIInput.run_id == run.id,
        )
    )
    if record is None:
        raise OperationError("run_input_unavailable")
    try:
        profile = Profile.model_validate(run.profile_snapshot)
        citations = [Citation.model_validate(c) for c in record.citations]
    except (ValidationError, ValueError, TypeError):
        raise OperationError("run_input_invalid") from None
    context: EditorContext | CopywritingContext | PlanningContext | None = None
    if run.profile == "content_planner":
        try:
            context = PlanningContext.model_validate(record.planner_context)
            validate_planning_context(context)
        except ValidationError:
            raise OperationError("planner_input_invalid") from None
        if (
            citations
            or record.post_id is not None
            or record.revision_id is not None
            or record.editor_context is not None
            or record.copy_context is not None
            or record.plan_id != context.plan.id
            or record.question != context.direction
            or context.brand_id != run.brand_id
            or record.actor_id != run.actor_id
        ):
            raise OperationError("planner_input_invalid")
        fresh_plan = await planning_snapshot(
            s,
            run.workspace_id,
            run.brand_id,
            context.plan.id,
            context.plan.content_hash,
            context.fact_ids,
            context.direction,
            context.knowledge_gaps,
        )
        if fresh_plan != context:
            raise OperationError("planner_context_changed")
    elif run.profile in {"editor", "copywriter"}:
        try:
            if run.profile == "copywriter":
                context = CopywritingContext.model_validate(record.copy_context)
                validate_context(context)
                source = context.source
                if record.editor_context is not None or record.question != context.direction:
                    raise OperationError("copywriter_input_invalid")
            else:
                context = EditorContext.model_validate(record.editor_context)
                source = context
                if record.copy_context is not None:
                    raise OperationError("editor_input_invalid")
        except ValidationError:
            raise OperationError("run_input_invalid") from None
        if (
            citations
            or source.post_id != record.post_id
            or source.revision.id != record.revision_id
            or source.brand_id != run.brand_id
            or record.actor_id != run.actor_id
            or record.plan_id is not None
            or record.planner_context is not None
        ):
            raise OperationError("editor_input_invalid")
        fresh = await snapshot(
            s,
            run.workspace_id,
            run.brand_id,
            source.post_id,
            source.revision.id,
            source.revision.content_hash,
            require_latest=require_latest,
        )
        if fresh != source:
            raise OperationError("editor_context_changed")
    elif (
        record.editor_context is not None
        or record.copy_context is not None
        or record.plan_id is not None
        or record.planner_context is not None
        or not 1 <= len(citations) <= 5
    ):
        raise OperationError("run_input_invalid")
    if not 1 <= len(record.question) <= 500:
        raise OperationError("run_input_invalid")
    safe_text(record.question)
    for c in citations:
        safe_text(c.text)
        current = await eligible_citation(s, run.workspace_id, c.chunk_id, run.brand_id)
        if current != c:
            raise OperationError("run_sources_changed")
    if canonical_hash(record.payload) != record.content_hash:
        raise OperationError("run_input_hash_mismatch")
    return record, profile, citations, context


def executable(
    settings: Settings,
    run: AIRun,
    record: AIInput,
    profile: Profile,
    citations: list[Citation],
    context: EditorContext | CopywritingContext | PlanningContext | None = None,
) -> None:
    if (
        settings.ai_provider == "disabled"
        or run.workspace_id not in settings.ai_allowed_workspaces
        or (run.provider, run.model) != (settings.ai_provider, settings.ai_model)
    ):
        raise OperationError("model_configuration_changed")
    current = next(p for p in PROFILES if p.name == run.profile)
    if run.profile_version_id is not None:
        # Registry permits only the bounded purpose; capabilities/schema remain code-owned.
        current = current.model_copy(update={"purpose": profile.purpose})
    if (
        profile != current
        or run.profile_version != profile.version
        or current.status != "testing"
        or current.blocked_reason
    ):
        raise OperationError("profile_contract_changed")
    payload = (
        planning_payload(profile, context.model_dump(mode="json"), run.model)
        if isinstance(context, PlanningContext)
        else copywriting_payload(profile, context.model_dump(mode="json"), run.model)
        if isinstance(context, CopywritingContext)
        else editorial_payload(profile, context.model_dump(mode="json"), run.model)
        if context
        else assessment_payload(profile, record.question, citations, run.model)
    )
    if payload != record.payload:
        raise OperationError("execution_contract_changed")
