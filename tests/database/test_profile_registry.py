"""Versioned testing selection is not human approval, provider permission or an AI role."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from smm_gpt.domain import profiles as d
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.ai import Profile
from smm_gpt.domain.knowledge import Citation
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIArtifact, AIRun
from smm_gpt.infrastructure.models import Membership
from smm_gpt.infrastructure.profile_models import AIProfileDecision, AIProfileHead, AIProfileVersion
from smm_gpt.services.ai import AIService
from smm_gpt.services.model_gateway import assessment_payload
from smm_gpt.services.profiles import ProfileService
from smm_gpt.workers.ai import process

from .conftest import TenantFixture
from .profile_fixtures import select_profile
from .test_ai_queue import Gateway, command, config, prepare
from .test_knowledge import activate, seed, submit

pytestmark = pytest.mark.integration


def draft(revision: int = 0) -> d.DraftProfile:
    return d.DraftProfile(
        idempotency_key=uuid4().hex,
        profile="product_expert",
        expected_revision=revision,
        purpose="Synthetic new purpose",
        model="synthetic-model",
        reason="Synthetic change",
    )


async def decide(
    t: TenantFixture, *, disable: bool = False, latest: bool = True
) -> d.ProfileReceipt:
    core = ProfileService(t.access)
    head = await core.read(t.owner, t.workspace, "product_expert", uuid4())
    version = head.latest if latest else head.testing
    assert version
    kind = d.DisableProfile if disable else d.SelectTesting
    return await core.execute(
        t.owner,
        t.workspace,
        kind(
            idempotency_key=uuid4().hex,
            profile="product_expert",
            expected_revision=head.revision,
            version_id=version.id,
            content_hash=version.content_hash,
            reason="Synthetic explicit human choice",
            human_confirmed=True,
        ),
        uuid4(),
    )


async def test_version_history_concurrency_replay_and_explicit_selection(
    tenants: TenantFixture,
) -> None:
    t, core = tenants, ProfileService(tenants.access)
    assert await core.registry(t.owner, t.workspace, uuid4()) == []
    cmd = draft()
    results = await asyncio.gather(
        *[core.execute(t.owner, t.workspace, cmd, uuid4()) for _ in range(3)]
    )
    assert results == [results[0]] * 3
    head = await core.read(t.owner, t.workspace, "product_expert", uuid4())
    assert head.revision == head.latest.number == 1 and head.testing is None
    assert head.latest.compatible and not head.decisions
    assert head.latest.profile_snapshot["allowed_capabilities"] == [
        "knowledge.search",
        "assessment.propose",
    ]
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await core.execute(
            t.owner, t.workspace, cmd.model_copy(update={"purpose": "Changed"}), uuid4()
        )
    with pytest.raises(OperationError, match="profile_revision_conflict"):
        await core.execute(t.owner, t.workspace, draft(), uuid4())
    selection = d.SelectTesting(
        idempotency_key=uuid4().hex,
        profile="product_expert",
        expected_revision=1,
        version_id=head.latest.id,
        content_hash=head.latest.content_hash,
        reason="Test this exact version",
        human_confirmed=True,
    )
    selected = await core.execute(t.owner, t.workspace, selection, uuid4())
    second = await core.execute(t.owner, t.workspace, draft(2), uuid4())
    head = await core.read(t.owner, t.workspace, "product_expert", uuid4())
    assert head.revision == 3 and head.latest.id == second.version_id and head.latest.number == 2
    assert (
        head.testing_version_id == selected.version_id
        and head.testing_selection_id == selected.decision_id
    )
    assert len(head.versions) == 2 and len(head.decisions) == 1
    assert not head.versions_truncated and not head.decisions_truncated
    await decide(t, disable=True, latest=False)
    assert await core.execute(t.owner, t.workspace, selection, uuid4()) == selected
    assert (await core.read(t.owner, t.workspace, "product_expert", uuid4())).testing is None
    assert (await core.read_version(t.owner, t.workspace, selected.version_id, uuid4())).number == 1
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(AIProfileVersion)) == 2
        assert await s.scalar(select(func.count()).select_from(AIProfileDecision)) == 2
        assert await s.scalar(select(func.count()).select_from(AIRun)) == 0


async def test_new_draft_keeps_dispatch_pinned_and_switch_hides_old_artifact(
    tenants: TenantFixture,
) -> None:
    t, core = tenants, ProfileService(tenants.access)
    settings, service, c, _ = await prepare(t)
    run = await service.start(t.owner, t.workspace, c, uuid4())
    await core.execute(t.owner, t.workspace, draft(2), uuid4())
    assert (
        run.profile_version_id == c.profile_version_id
        and run.profile_selection_id == c.profile_selection_id
    )
    gateway = Gateway()
    assert await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    assert (await service.read(t.owner, t.workspace, run.id, uuid4())).assessment
    inputs = await service.inputs(t.owner, t.workspace, run.id, uuid4())
    assert "Synthetic reference assessment" in str(inputs.payload["instructions"])
    assert "Synthetic new purpose" not in str(inputs.payload["instructions"])
    selected = await decide(t)
    assert selected.version_id != run.profile_version_id
    stale = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert stale.assessment is None and stale.error_code == "artifact_profile_stale_or_unavailable"
    assert await service.inputs(t.owner, t.workspace, run.id, uuid4()) == inputs
    assert (await service.start(t.owner, t.workspace, c, uuid4())).id == run.id
    assert gateway.calls == 1


@pytest.mark.parametrize("disable", [False, True])
async def test_selection_change_during_dispatch_discards_output(
    tenants: TenantFixture, disable: bool
) -> None:
    t, core = tenants, ProfileService(tenants.access)
    settings, service, c, _ = await prepare(t)
    run = await service.start(t.owner, t.workspace, c, uuid4())
    await core.execute(t.owner, t.workspace, draft(2), uuid4())
    gateway = Gateway(pause=True)
    task = asyncio.create_task(
        process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    )
    await asyncio.wait_for(gateway.entered.wait(), 5)
    try:
        await decide(t, disable=disable, latest=not disable)
    finally:
        gateway.release.set()
    assert not await task
    ended = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert ended.state == "failed" and ended.assessment is None
    assert ended.error_code == (
        "profile_testing_not_selected" if disable else "profile_selection_changed"
    )
    assert ended.usage["input_tokens"] == 20 and ended.usage["attempts"] == 1
    assert ended.usage["cost_usd"] is None and gateway.calls == 1
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(AIArtifact)) == 0


async def test_reenable_same_version_does_not_revive_queued_work(tenants: TenantFixture) -> None:
    t = tenants
    settings, service, c, _ = await prepare(t)
    run = await service.start(t.owner, t.workspace, c, uuid4())
    await decide(t, disable=True)
    selected = await decide(t)
    assert (
        selected.version_id == c.profile_version_id
        and selected.decision_id != c.profile_selection_id
    )
    gateway = Gateway()
    assert not await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    assert (
        await service.read(t.owner, t.workspace, run.id, uuid4())
    ).error_code == "profile_selection_changed"
    assert gateway.calls == 0
    fresh = c.model_copy(
        update={"idempotency_key": uuid4().hex, "profile_selection_id": selected.decision_id}
    )
    created = await service.start(t.owner, t.workspace, fresh, uuid4())
    assert created.state == "queued"
    assert await process(t.worker, settings, gateway, t.workspace, created.id, t.owner.user_id)
    assert gateway.calls == 1


async def test_no_fallback_wrong_model_unsupported_profile_and_exact_hash(
    tenants: TenantFixture,
) -> None:
    t, core = tenants, ProfileService(tenants.access)
    settings = config(t.workspace)
    settings.ai_daily_run_limit = 20
    service = AIService(t.access, settings)
    bid = await seed(t)
    doc, _ = await submit(t, bid)
    await activate(t, doc)
    absent = await service.start(t.owner, t.workspace, command(bid), uuid4())
    assert absent.state == "blocked" and absent.error_code == "profile_testing_not_selected"
    selected = await select_profile(t)
    missing = await service.start(t.owner, t.workspace, command(bid), uuid4())
    assert missing.state == "blocked" and missing.error_code == "profile_selection_required"
    bad = command(bid).model_copy(
        update={"profile_version_id": selected.version_id, "profile_selection_id": uuid4()}
    )
    assert (
        await service.start(t.owner, t.workspace, bad, uuid4())
    ).error_code == "profile_selection_changed"
    head = await core.read(t.owner, t.workspace, "product_expert", uuid4())
    with pytest.raises(OperationError, match="profile_version_changed"):
        await core.execute(
            t.owner,
            t.workspace,
            d.SelectTesting(
                idempotency_key=uuid4().hex,
                profile="product_expert",
                expected_revision=2,
                version_id=selected.version_id,
                content_hash="f" * 64,
                reason="Wrong hash",
                human_confirmed=True,
            ),
            uuid4(),
        )
    await core.execute(
        t.owner, t.workspace, draft(2).model_copy(update={"model": "different-model"}), uuid4()
    )
    other_model = await decide(t)
    c = command(bid).model_copy(
        update={
            "profile_version_id": other_model.version_id,
            "profile_selection_id": other_model.decision_id,
        }
    )
    assert (
        await service.start(t.owner, t.workspace, c, uuid4())
    ).error_code == "profile_model_changed"
    unsupported = await core.execute(
        t.owner, t.workspace, draft().model_copy(update={"profile": "visual_creator"}), uuid4()
    )
    definition = await core.read_version(t.owner, t.workspace, unsupported.version_id, uuid4())
    assert definition.blocked_reason == "media_rights_pipeline_required"
    with pytest.raises(OperationError, match="profile_implementation_unavailable"):
        await core.execute(
            t.owner,
            t.workspace,
            d.SelectTesting(
                idempotency_key=uuid4().hex,
                profile="visual_creator",
                expected_revision=1,
                version_id=unsupported.version_id,
                content_hash=definition.content_hash,
                reason="Not an implementation",
                human_confirmed=True,
            ),
            uuid4(),
        )
    assert head.latest.profile_snapshot["status"] == "testing"


async def test_changed_prompt_contract_blocks_existing_and_new_selection(
    tenants: TenantFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    t, core = tenants, ProfileService(tenants.access)
    settings, service, c, _ = await prepare(t)
    run = await service.start(t.owner, t.workspace, c, uuid4())

    def changed(
        profile: Profile, question: str, citations: list[Citation], model: str
    ) -> dict[str, object]:
        return {**assessment_payload(profile, question, citations, model), "new_contract": True}

    monkeypatch.setattr("smm_gpt.services.profiles.assessment_payload", changed)
    detail = await core.read(t.owner, t.workspace, "product_expert", uuid4())
    assert (
        not detail.latest.compatible and detail.latest.blocked_reason == "profile_contract_changed"
    )
    with pytest.raises(OperationError, match="profile_contract_changed"):
        await decide(t)
    gateway = Gateway()
    assert not await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    assert (
        await service.read(t.owner, t.workspace, run.id, uuid4())
    ).error_code == "profile_contract_changed"
    assert gateway.calls == 0


async def test_owner_mfa_rls_immutable_history_and_direct_db_bypass(tenants: TenantFixture) -> None:
    t, core = tenants, ProfileService(tenants.access)
    selected = await select_profile(t)
    for role in ["viewer", "editor", "strategist", "administrator", "publisher"]:
        async with t.admin.transaction() as s:
            await s.execute(
                update(Membership).where(Membership.user_id == t.viewer.user_id).values(role=role)
            )
        with pytest.raises(AccessDenied):
            await core.execute(t.viewer, t.workspace, draft(2), uuid4())
        with pytest.raises(AccessDenied):
            await core.registry(t.viewer, t.workspace, uuid4())
        async with t.runtime.transaction(t.viewer.user_id, t.workspace) as s:
            assert await s.scalar(select(func.count()).select_from(AIProfileVersion)) == 0
    with pytest.raises(AccessDenied):
        await core.execute(replace(t.owner, mfa=False), t.workspace, draft(2), uuid4())
    with pytest.raises(AccessDenied):
        await core.read_version(t.other, t.workspace, selected.version_id, uuid4())
    with pytest.raises(OperationError, match="not_found"):
        await core.read_version(t.other, t.other_workspace, selected.version_id, uuid4())
    with pytest.raises(OperationError, match="not_found"):
        await core.read(t.other, t.other_workspace, "product_expert", uuid4())
    for database, sql in [
        (t.worker, "UPDATE ai_profile_heads SET revision=revision+1"),
        (t.worker, "INSERT INTO ai_profile_versions SELECT * FROM ai_profile_versions"),
        (t.worker, "INSERT INTO ai_profile_decisions SELECT * FROM ai_profile_decisions"),
        (
            t.runtime,
            "UPDATE ai_profile_heads SET revision=revision+1,"
            "testing_version_id=NULL,testing_selection_id=NULL",
        ),
        (t.runtime, "UPDATE ai_profile_versions SET model='different-model'"),
        (t.admin, "UPDATE ai_profile_versions SET model='different-model'"),
        (t.admin, "DELETE FROM ai_profile_decisions"),
        (t.admin, "TRUNCATE ai_profile_receipts"),
        (t.admin, "DELETE FROM ai_profile_heads"),
    ]:
        with pytest.raises(DBAPIError):
            async with database.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(sql))
    bid = await seed(t)
    with pytest.raises(DBAPIError, match="registered_testing_profile_required"):
        async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
            s.add(
                AIRun(
                    workspace_id=t.workspace,
                    actor_id=t.owner.user_id,
                    identity_id=t.owner.identity_id,
                    brand_id=bid,
                    key_hash="a" * 64,
                    request_hash="b" * 64,
                    profile="product_expert",
                    profile_version="reference-assessment-v1",
                    profile_snapshot={},
                    state="queued",
                    provider="openai",
                    model="synthetic-model",
                    usage={},
                )
            )
    async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
        assert await s.scalar(select(AIProfileHead.testing_version_id)) == selected.version_id
