"""Shared queue invariants. No human principal, external I/O or state mutations."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.core.config import Settings
from smm_gpt.domain.ai import PROFILES, Profile
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.knowledge import Citation
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIInput, AIRun
from smm_gpt.infrastructure.models import Identity, Membership, User
from smm_gpt.services.knowledge import eligible_citation
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.model_gateway import assessment_payload


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


async def current_input(s: AsyncSession, run: AIRun) -> tuple[AIInput, Profile, list[Citation]]:
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
    if not 1 <= len(citations) <= 5 or not 1 <= len(record.question) <= 500:
        raise OperationError("run_input_invalid")
    safe_text(record.question)
    for c in citations:
        safe_text(c.text)
        current = await eligible_citation(s, run.workspace_id, c.chunk_id, run.brand_id)
        if current != c:
            raise OperationError("run_sources_changed")
    if canonical_hash(record.payload) != record.content_hash:
        raise OperationError("run_input_hash_mismatch")
    return record, profile, citations


def executable(
    settings: Settings, run: AIRun, record: AIInput, profile: Profile, citations: list[Citation]
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
    payload = assessment_payload(profile, record.question, citations, run.model)
    if payload != record.payload:
        raise OperationError("execution_contract_changed")
