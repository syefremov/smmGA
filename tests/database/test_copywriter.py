import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from smm_gpt.core.config import Settings
from smm_gpt.domain import content as c
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.ai import CancelAssessment, Profile, RunAssessment
from smm_gpt.domain.copywriter import CopywritingContext, RunCopyDraft
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIArtifact, AIInput, AIRun
from smm_gpt.infrastructure.content_models import ContentDecision, PostRevision
from smm_gpt.infrastructure.models import Membership
from smm_gpt.services.ai import AIService
from smm_gpt.services.model_gateway import CopywritingGatewayResult
from smm_gpt.workers.ai import process, reconcile

from ..copywriter_fixtures import draft_fixture
from .conftest import TenantFixture
from .profile_fixtures import select_profile
from .test_ai_queue import Gateway, config
from .test_content import Pilot, pilot
from .test_editor import change_policy, change_revision

pytestmark = pytest.mark.integration


class CopyGateway:
    def __init__(self, pause: bool = False, outcome: str = "ok"):
        self.calls, self.outcome = 0, outcome
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        if not pause:
            self.release.set()

    async def draft(
        self, profile: Profile, context: CopywritingContext
    ) -> CopywritingGatewayResult:
        self.calls += 1
        assert profile.name == "copywriter" and profile.output_schema == "CopyDraft"
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), 10)
        if self.outcome == "unknown":
            raise OperationError("model_outcome_unknown")
        result = draft_fixture(context)
        if self.outcome == "invalid":
            result.context_hash = "f" * 64
        return CopywritingGatewayResult(
            draft=result,
            model="synthetic-model",
            response_id="synthetic-response",
            input_tokens=20,
            output_tokens=10,
        )


async def prepare(t: TenantFixture) -> tuple[Settings, AIService, RunCopyDraft, Pilot]:
    p = await pilot(t)
    selected = await select_profile(t, "copywriter", "Synthetic copy draft")
    revision = (await p.post()).revisions[0]
    cfg = config(t.workspace)
    cmd = RunCopyDraft(
        idempotency_key=uuid4().hex,
        brand_id=p.brand,
        post_id=p.post_id,
        revision_id=revision.id,
        content_hash=revision.content_hash,
        direction="Make it concise",
        profile_version_id=selected.version_id,
        profile_selection_id=selected.decision_id,
        testing_only=True,
    )
    return cfg, AIService(t.access, cfg), cmd, p


async def test_copy_queue_once_current_provenance_no_content_mutation(
    tenants: TenantFixture,
) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    before = await p.post()
    runs = await asyncio.gather(
        *[service.start(t.owner, t.workspace, cmd, uuid4()) for _ in range(3)]
    )
    assert len({r.id for r in runs}) == 1 and runs[0].state == "queued"
    run = runs[0]
    inputs = await service.inputs(t.owner, t.workspace, run.id, uuid4())
    assert inputs.copy_context and not inputs.editor_context and not inputs.citations
    assert inputs.copy_context.source.revision.id == cmd.revision_id
    assert inputs.question == inputs.copy_context.direction == cmd.direction
    copy, reference = CopyGateway(), Gateway()
    results = await asyncio.gather(
        *[
            process(
                t.worker,
                cfg,
                reference,
                t.workspace,
                run.id,
                t.owner.user_id,
                copywriting_gateway=copy,
            )
            for _ in range(2)
        ]
    )
    assert sorted(results) == [False, True] and copy.calls == 1 and reference.calls == 0
    result = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert result.copy_draft == draft_fixture(inputs.copy_context)
    assert result.assessment is None and result.editorial_review is None
    assert result.state == "needs_review" and result.retrieval_run_id is None
    after = await p.post()
    assert (before.version, before.state, before.revisions) == (
        after.version,
        after.state,
        after.revisions,
    )
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(ContentDecision)) == 0
        assert await s.scalar(select(func.count()).select_from(PostRevision)) == 1
    assert (await service.start(t.owner, t.workspace, cmd, uuid4())).id == run.id
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await service.start(
            t.owner, t.workspace, cmd.model_copy(update={"direction": "Other"}), uuid4()
        )
    await change_revision(p)
    stale = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert (
        stale.copy_draft is None and stale.error_code == "artifact_copywriter_stale_or_unavailable"
    )
    assert await service.inputs(t.owner, t.workspace, run.id, uuid4()) == inputs
    # History cannot be silently dropped by downgrade, even after the artifact goes stale.
    with pytest.raises(DBAPIError, match="copywriter_history_requires_restore_plan"):
        await asyncio.to_thread(
            alembic_command.downgrade, Config("alembic.ini"), "0013_editor_triage"
        )
    async with t.admin.transaction() as s:
        assert await s.scalar(text("SELECT version_num FROM alembic_version")) == "0018_text_files"
        assert await s.scalar(select(func.count()).select_from(AIArtifact)) == 1


@pytest.mark.parametrize("change", ["revision", "policy", "expiry", "cancel", "authorization"])
async def test_copy_stale_or_cancelled_queue_no_dispatch(
    tenants: TenantFixture,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    if change == "expiry":
        monkeypatch.setattr("smm_gpt.services.content_records.utcnow", lambda: p.source.expires_at)
    elif change == "cancel":
        await service.cancel(
            t.owner,
            t.workspace,
            run.id,
            CancelAssessment(
                idempotency_key=uuid4().hex,
                expected_version=run.version,
            ),
            uuid4(),
        )
    elif change == "authorization":
        async with t.admin.transaction() as s:
            await s.execute(
                update(Membership).where(Membership.user_id == t.owner.user_id).values(active=False)
            )
    else:
        await (change_revision(p) if change == "revision" else change_policy(p))
    copy = CopyGateway()
    assert not await process(
        t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, copywriting_gateway=copy
    )
    assert copy.calls == 0
    if change == "authorization":
        # RLS hides revoked actors' rows; the restricted reconciler marks them, not dispatch.
        assert await reconcile(t.worker) == 1
    async with t.admin.transaction() as s:
        stored = await s.get(AIRun, run.id)
        assert stored and stored.state == ("cancelled" if change == "cancel" else "blocked")


@pytest.mark.parametrize("change", ["revision", "policy", "cancel", "profile"])
async def test_copy_discards_changed_inflight_inputs_with_known_usage(
    tenants: TenantFixture,
    change: str,
) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    copy = CopyGateway(pause=True)
    task = asyncio.create_task(
        process(
            t.worker,
            cfg,
            Gateway(),
            t.workspace,
            run.id,
            t.owner.user_id,
            copywriting_gateway=copy,
        )
    )
    await asyncio.wait_for(copy.entered.wait(), 5)
    try:
        if change == "profile":
            # Same profile version reselected still invalidates the old selection identity.
            from smm_gpt.domain.profiles import SelectTesting
            from smm_gpt.services.profiles import ProfileService

            profiles = ProfileService(t.access)
            head = await profiles.read(t.owner, t.workspace, "copywriter", uuid4())
            await profiles.execute(
                t.owner,
                t.workspace,
                SelectTesting(
                    idempotency_key=uuid4().hex,
                    profile="copywriter",
                    expected_revision=head.revision,
                    version_id=head.latest.id,
                    content_hash=head.latest.content_hash,
                    reason="New explicit testing selection",
                    human_confirmed=True,
                ),
                uuid4(),
            )
        elif change == "cancel":
            current = await service.read(t.owner, t.workspace, run.id, uuid4())
            await service.cancel(
                t.owner,
                t.workspace,
                run.id,
                CancelAssessment(
                    idempotency_key=uuid4().hex,
                    expected_version=current.version,
                ),
                uuid4(),
            )
        else:
            await (change_revision(p) if change == "revision" else change_policy(p))
    finally:
        copy.release.set()
    assert not await task
    result = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert result.state == ("cancelled" if change == "cancel" else "failed")
    assert result.usage["input_tokens"] == 20 and result.copy_draft is None
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(AIArtifact)) == 0


@pytest.mark.parametrize("outcome", ["invalid", "unknown"])
async def test_copy_invalid_and_unknown_are_not_retried(
    tenants: TenantFixture, outcome: str
) -> None:
    t = tenants
    cfg, service, cmd, _ = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    copy = CopyGateway(outcome=outcome)
    assert not await process(
        t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, copywriting_gateway=copy
    )
    result = await service.start(t.owner, t.workspace, cmd, uuid4())
    assert result.state == ("failed" if outcome == "invalid" else "unknown")
    assert result.copy_draft is None
    assert not await process(
        t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, copywriting_gateway=copy
    )
    assert copy.calls == 1


async def test_copy_permissions_gates_and_immutable_inputs(tenants: TenantFixture) -> None:
    t = tenants
    _, service, cmd, p = await prepare(t)
    for actor in (t.viewer, t.other, replace(t.owner, mfa=False)):
        with pytest.raises(AccessDenied):
            await service.start(actor, t.workspace, cmd, uuid4())
    disabled = AIService(t.access, Settings(_env_file=None))
    assert (
        await disabled.start(t.owner, t.workspace, cmd, uuid4())
    ).error_code == "model_provider_disabled"
    cmd = cmd.model_copy(update={"idempotency_key": uuid4().hex})
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    for actor, wid in [(t.other, t.other_workspace), (t.viewer, t.workspace)]:
        with pytest.raises((OperationError, AccessDenied)):
            await service.inputs(actor, wid, run.id, uuid4())
    async with t.runtime.transaction(t.viewer.user_id, t.workspace) as s:
        assert await s.scalar(select(func.count()).select_from(AIInput)) == 0
    for database, sql in [
        (t.worker, "UPDATE posts SET title='changed'"),
        (t.worker, "INSERT INTO content_decisions SELECT * FROM content_decisions"),
        (t.admin, "UPDATE ai_inputs SET copy_context=NULL"),
        (t.admin, "DELETE FROM ai_inputs"),
    ]:
        with pytest.raises(DBAPIError):
            async with database.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(sql))
    old = RunAssessment(
        idempotency_key=uuid4().hex,
        profile="copywriter",
        brand_id=p.brand,
        question="not a typed revision",
        testing_only=True,
    )
    assert (
        await service.start(t.owner, t.workspace, old, uuid4())
    ).error_code == "copywriter_revision_request_required"
    missing = cmd.model_copy(update={"idempotency_key": uuid4().hex, "revision_id": uuid4()})
    assert (
        await service.start(t.owner, t.workspace, missing, uuid4())
    ).error_code == "editor_revision_unavailable"
    post = await p.post()
    await p.run(
        c.SaveRevision(
            post_id=p.post_id,
            expected_version=post.version,
            body=p.body().model_copy(update={"fact_ids": []}),
            idempotency_key=uuid4().hex,
        )
    )
    revision = (await p.post()).revisions[0]
    no_facts = cmd.model_copy(
        update={
            "idempotency_key": uuid4().hex,
            "revision_id": revision.id,
            "content_hash": revision.content_hash,
        }
    )
    assert (
        await service.start(t.owner, t.workspace, no_facts, uuid4())
    ).error_code == "copywriter_confirmed_facts_required"


async def test_copy_db_guards_cannot_cross_bind_profiles_or_current_revision(
    tenants: TenantFixture,
) -> None:
    t = tenants
    _, service, cmd, p = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    async with t.admin.transaction() as s:
        row = await s.scalar(select(AIInput).where(AIInput.run_id == run.id))
        assert row
        original = {col.name: getattr(row, col.name) for col in AIInput.__table__.columns}
    for change in ["missing", "editor", "brand"]:
        values = {**original, "id": uuid4()}
        if change == "missing":
            values["copy_context"] = None
        elif change == "editor":
            values["editor_context"], values["copy_context"] = values["copy_context"], None
        else:
            inputs = await service.inputs(t.owner, t.workspace, run.id, uuid4())
            assert inputs.copy_context
            inputs.copy_context.source.brand_id = uuid4()
            values["copy_context"] = inputs.copy_context.model_dump(mode="json")
        with pytest.raises(DBAPIError, match="copywriter_input_required"):
            async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
                s.add(AIInput(**values))
    await change_revision(p)
    with pytest.raises(DBAPIError, match="copywriter_current_input_required"):
        async with t.admin.transaction() as s:
            await s.execute(
                update(AIRun)
                .where(AIRun.id == run.id)
                .values(
                    state="running",
                    version=2,
                    lease_id=uuid4(),
                    lease_until=func.now() + text("interval '2 minutes'"),
                )
            )
