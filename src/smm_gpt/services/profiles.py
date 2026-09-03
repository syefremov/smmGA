"""Shared personal registry commands; never a provider call or production activation."""

from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import ai
from smm_gpt.domain import profiles as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIRun
from smm_gpt.infrastructure.profile_models import (
    AIProfileDecision,
    AIProfileHead,
    AIProfileReceipt,
    AIProfileVersion,
)
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.knowledge import lock
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.model_gateway import assessment_payload


def template(name: str) -> ai.Profile:
    value = next((p for p in ai.PROFILES if p.name == name), None)
    if value is None:
        raise OperationError("profile_implementation_unavailable")
    return value


def execution_hash(profile: ai.Profile, provider: str, model: str) -> str:
    # Detect changed gateway instructions/schema/parameters without using real inputs or a model.
    return canonical_hash(
        {
            "contract": "profile-execution-v1",
            "provider": provider,
            "profile": profile.model_dump(mode="json"),
            "payload": assessment_payload(profile, "registry-contract-probe", [], model),
        }
    )


def version_hash(row: AIProfileVersion) -> str:
    return canonical_hash(
        {
            "contract": "profile-version-v1",
            "workspace_id": str(row.workspace_id),
            "actor_id": str(row.actor_id),
            "profile": row.profile,
            "number": row.number,
            "provider": row.provider,
            "model": row.model,
            "profile_snapshot": row.profile_snapshot,
            "execution_hash": row.execution_hash,
            "reason": row.reason,
        }
    )


def compatible_profile(row: AIProfileVersion) -> ai.Profile:
    try:
        profile = ai.Profile.model_validate(row.profile_snapshot)
    except ValidationError:
        raise OperationError("profile_contract_changed") from None
    current = template(row.profile).model_copy(update={"purpose": profile.purpose})
    if (
        profile != current
        or row.provider != "openai"
        or row.content_hash != version_hash(row)
        or row.execution_hash != execution_hash(profile, row.provider, row.model)
    ):
        raise OperationError("profile_contract_changed")
    safe_text(profile.purpose)
    return profile


def version_view(row: AIProfileVersion) -> d.ProfileVersionView:
    view = d.ProfileVersionView.model_validate(row)
    try:
        profile = compatible_profile(row)
        view.compatible = True
        view.blocked_reason = profile.blocked_reason
    except OperationError as exc:
        view.blocked_reason = exc.code
    return view


async def head_row(s: AsyncSession, wid: UUID, name: str) -> AIProfileHead | None:
    row: AIProfileHead | None = await s.scalar(
        select(AIProfileHead).where(
            AIProfileHead.workspace_id == wid,
            AIProfileHead.profile == name,
        )
    )
    return row


async def version_row(s: AsyncSession, wid: UUID, vid: UUID) -> AIProfileVersion:
    row = await s.scalar(
        select(AIProfileVersion).where(
            AIProfileVersion.workspace_id == wid,
            AIProfileVersion.id == vid,
        )
    )
    if row is None:
        raise OperationError("not_found", 404)
    return row


async def selected_version(
    s: AsyncSession, wid: UUID, name: str, vid: UUID | None, sid: UUID | None
) -> AIProfileVersion:
    head = await head_row(s, wid, name)
    if head is None or head.testing_version_id is None:
        raise OperationError("profile_testing_not_selected")
    if vid is None or sid is None:
        raise OperationError("profile_selection_required")
    if (vid, sid) != (head.testing_version_id, head.testing_selection_id):
        raise OperationError("profile_selection_changed")
    row = await version_row(s, wid, vid)
    profile = compatible_profile(row)
    if profile.status != "testing" or profile.blocked_reason:
        raise OperationError("profile_implementation_unavailable")
    return row


async def assert_registered_run(s: AsyncSession, run: AIRun) -> None:
    row = await selected_version(
        s,
        run.workspace_id,
        run.profile,
        run.profile_version_id,
        run.profile_selection_id,
    )
    if row.profile_snapshot != run.profile_snapshot or row.provider != run.provider:
        raise OperationError("profile_contract_changed")
    # The worker stores the provider-returned model identity only AFTER dispatch/final checks.
    if run.state in {"queued", "running", "cancel_requested"} and row.model != run.model:
        raise OperationError("profile_model_changed")


class ProfileService:
    def __init__(self, access: AccessService):
        self.access = access

    async def execute(
        self, actor: Principal, wid: UUID, command: d.ProfileCommand, request: UUID
    ) -> d.ProfileReceipt:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            await lock(s, wid)
            fingerprint = canonical_hash(
                command.model_dump(mode="json", exclude={"idempotency_key"})
            )
            previous = await s.scalar(
                select(AIProfileReceipt).where(
                    AIProfileReceipt.workspace_id == wid,
                    AIProfileReceipt.actor_id == actor.user_id,
                    AIProfileReceipt.key_hash == digest(command.idempotency_key),
                )
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.ProfileReceipt.model_validate(previous.result)
            safe_text(command.reason)
            head = await head_row(s, wid, command.profile)
            if (head.revision if head else 0) != command.expected_revision:
                raise OperationError("profile_revision_conflict")
            if isinstance(command, d.DraftProfile):
                safe_text(command.purpose)
                safe_text(command.model)
                profile = template(command.profile).model_copy(update={"purpose": command.purpose})
                previous_version = (
                    await version_row(s, wid, head.latest_version_id) if head else None
                )
                row = AIProfileVersion(
                    id=uuid4(),
                    workspace_id=wid,
                    profile=command.profile,
                    number=previous_version.number + 1 if previous_version else 1,
                    actor_id=actor.user_id,
                    provider=command.provider,
                    model=command.model,
                    profile_snapshot=profile.model_dump(mode="json"),
                    execution_hash=execution_hash(profile, command.provider, command.model),
                    reason=command.reason,
                )
                row.content_hash = version_hash(row)
                s.add(row)
                await s.flush()
                if head:
                    head.latest_version_id = row.id
                    head.revision += 1
                else:
                    head = AIProfileHead(
                        workspace_id=wid,
                        profile=command.profile,
                        revision=1,
                        latest_version_id=row.id,
                        testing_version_id=None,
                        testing_selection_id=None,
                    )
                    s.add(head)
                receipt = d.ProfileReceipt(
                    profile=command.profile, revision=head.revision, version_id=row.id
                )
            else:
                if head is None:
                    raise OperationError("not_found", 404)
                row = await version_row(s, wid, command.version_id)
                if row.profile != command.profile or row.content_hash != command.content_hash:
                    raise OperationError("profile_version_changed")
                if isinstance(command, d.SelectTesting):
                    profile = compatible_profile(row)
                    if profile.status != "testing" or profile.blocked_reason:
                        raise OperationError("profile_implementation_unavailable")
                elif head.testing_version_id != row.id:
                    raise OperationError("profile_selection_changed")
                decision = AIProfileDecision(
                    id=uuid4(),
                    workspace_id=wid,
                    profile=command.profile,
                    actor_id=actor.user_id,
                    version_id=row.id,
                    action=command.action,
                    revision=head.revision + 1,
                    content_hash=row.content_hash,
                    reason=command.reason,
                )
                s.add(decision)
                await s.flush()
                head.revision += 1
                head.testing_version_id = row.id if isinstance(command, d.SelectTesting) else None
                head.testing_selection_id = (
                    decision.id if isinstance(command, d.SelectTesting) else None
                )
                receipt = d.ProfileReceipt(
                    profile=command.profile,
                    revision=head.revision,
                    version_id=row.id,
                    decision_id=decision.id,
                )
            s.add(
                AIProfileReceipt(
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    key_hash=digest(command.idempotency_key),
                    request_hash=fingerprint,
                    result=receipt.model_dump(mode="json"),
                )
            )
            audit(s, actor.user_id, wid, request, command.action, "succeeded", receipt.version_id)
            return receipt

    async def registry(
        self, actor: Principal, wid: UUID, request: UUID
    ) -> list[d.RegisteredProfile]:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            rows = await s.scalars(
                select(AIProfileHead)
                .where(AIProfileHead.workspace_id == wid)
                .order_by(AIProfileHead.profile)
            )
            return [d.RegisteredProfile.model_validate(row) for row in rows]

    async def read_version(
        self, actor: Principal, wid: UUID, vid: UUID, request: UUID
    ) -> d.ProfileVersionView:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            return version_view(await version_row(s, wid, vid))

    async def read(
        self, actor: Principal, wid: UUID, name: ai.ProfileName, request: UUID
    ) -> d.ProfileDetail:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            head = await head_row(s, wid, name)
            if head is None:
                raise OperationError("not_found", 404)
            versions = list(
                (
                    await s.scalars(
                        select(AIProfileVersion)
                        .where(
                            AIProfileVersion.workspace_id == wid,
                            AIProfileVersion.profile == name,
                        )
                        .order_by(AIProfileVersion.number.desc())
                        .limit(21)
                    )
                ).all()
            )
            decisions = list(
                (
                    await s.scalars(
                        select(AIProfileDecision)
                        .where(
                            AIProfileDecision.workspace_id == wid,
                            AIProfileDecision.profile == name,
                        )
                        .order_by(AIProfileDecision.revision.desc())
                        .limit(21)
                    )
                ).all()
            )
            return d.ProfileDetail(
                **d.RegisteredProfile.model_validate(head).model_dump(),
                latest=version_view(await version_row(s, wid, head.latest_version_id)),
                testing=version_view(await version_row(s, wid, head.testing_version_id))
                if head.testing_version_id
                else None,
                versions=[version_view(row) for row in versions[:20]],
                decisions=[d.ProfileDecisionView.model_validate(row) for row in decisions[:20]],
                versions_truncated=len(versions) > 20,
                decisions_truncated=len(decisions) > 20,
            )
