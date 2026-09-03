import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command as migration
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from smm_gpt.core.config import Settings
from smm_gpt.domain import content as c
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.ai import CancelAssessment, Profile, RunAssessment
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.planner import PlanningContext, RunPlanDraft
from smm_gpt.infrastructure.ai_models import AIArtifact, AIInput, AIRun
from smm_gpt.infrastructure.content_models import ContentDecision, ContentRecord, PostRevision
from smm_gpt.infrastructure.models import Membership
from smm_gpt.services.ai import AIService
from smm_gpt.services.model_gateway import PlanningGatewayResult
from smm_gpt.workers.ai import process

from ..planner_fixtures import draft_fixture
from .conftest import TenantFixture
from .profile_fixtures import select_profile
from .test_ai_queue import Gateway, config
from .test_content import Pilot, pilot
from .test_editor import change_policy

pytestmark = pytest.mark.integration


class PlanGateway:
    def __init__(self, pause: bool = False, outcome: str = "ok"):
        self.calls, self.outcome = 0, outcome
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        if not pause:
            self.release.set()

    async def plan(self, profile: Profile, context: PlanningContext) -> PlanningGatewayResult:
        self.calls += 1
        assert profile.name == "content_planner" and profile.output_schema == "PlanDraft"
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), 10)
        if self.outcome == "unknown":
            raise OperationError("model_outcome_unknown")
        result = draft_fixture(context)
        if self.outcome == "invalid":
            result.slots[0].owner_id = uuid4()
        return PlanningGatewayResult(
            draft=result,
            model="synthetic-model",
            response_id="synthetic-response",
            input_tokens=20,
            output_tokens=10,
        )


async def plan_command(p: Pilot) -> RunPlanDraft:
    t = p.t
    selected = await select_profile(t, "content_planner", "Synthetic planning proposal")
    async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
        row = await s.scalar(
            select(ContentRecord)
            .where(
                ContentRecord.workspace_id == t.workspace,
                ContentRecord.brand_id == p.brand,
                ContentRecord.kind == "content_plan",
            )
            .order_by(ContentRecord.number.desc())
        )
        assert row
        return RunPlanDraft(
            idempotency_key=uuid4().hex,
            brand_id=p.brand,
            plan_id=row.id,
            content_hash=row.content_hash,
            fact_ids=[p.fact.id],
            direction="Explain confirmed facts",
            knowledge_gaps=["Missing promotion dates"],
            profile_version_id=selected.version_id,
            profile_selection_id=selected.decision_id,
            testing_only=True,
        )


async def prepare(t: TenantFixture) -> tuple[Settings, AIService, RunPlanDraft, Pilot]:
    p = await pilot(t)
    cfg = config(t.workspace)
    return cfg, AIService(t.access, cfg), await plan_command(p), p


async def change_intent(p: Pilot, cmd: RunPlanDraft, kind: str) -> c.RecordView:
    plan = await p.core.read_record(p.t.owner, p.t.workspace, cmd.plan_id, uuid4())
    assert isinstance(plan.body, c.ContentPlan)
    original = (
        plan
        if kind == "plan"
        else await p.core.read_record(
            p.t.owner,
            p.t.workspace,
            plan.body.campaign_id,
            uuid4(),
        )
    )
    result = await p.run(
        c.CreateRecord(
            body=original.body,
            replaces_id=original.id,
            expires_at=original.expires_at,
            idempotency_key=uuid4().hex,
        )
    )
    return await p.core.read_record(p.t.owner, p.t.workspace, result.entity_id, uuid4())


async def test_plan_queue_once_provenance_no_content_mutations(tenants: TenantFixture) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    before = await p.post()
    async with t.admin.transaction() as s:
        record_count = await s.scalar(select(func.count()).select_from(ContentRecord))
    runs = await asyncio.gather(
        *[service.start(t.owner, t.workspace, cmd, uuid4()) for _ in range(3)]
    )
    assert len({r.id for r in runs}) == 1 and runs[0].state == "queued"
    run = runs[0]
    inputs = await service.inputs(t.owner, t.workspace, run.id, uuid4())
    assert inputs.planner_context and not inputs.citations
    assert not inputs.copy_context and not inputs.editor_context
    assert inputs.planner_context.plan.id == cmd.plan_id
    planner, reference = PlanGateway(), Gateway()
    results = await asyncio.gather(
        *[
            process(
                t.worker,
                cfg,
                reference,
                t.workspace,
                run.id,
                t.owner.user_id,
                planning_gateway=planner,
            )
            for _ in range(2)
        ]
    )
    assert sorted(results) == [False, True] and planner.calls == 1 and reference.calls == 0
    result = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert result.plan_draft == draft_fixture(inputs.planner_context)
    assert (
        result.assessment is None and result.copy_draft is None and result.editorial_review is None
    )
    assert result.state == "needs_review" and result.retrieval_run_id is None
    after = await p.post()
    assert (before.version, before.state, before.revisions) == (
        after.version,
        after.state,
        after.revisions,
    )
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(ContentRecord)) == record_count
        assert await s.scalar(select(func.count()).select_from(ContentDecision)) == 0
        assert await s.scalar(select(func.count()).select_from(PostRevision)) == 1
    assert (await service.start(t.owner, t.workspace, cmd, uuid4())).id == run.id
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await service.start(
            t.owner, t.workspace, cmd.model_copy(update={"direction": "Other"}), uuid4()
        )
    await change_intent(p, cmd, "plan")
    stale = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert stale.plan_draft is None and stale.error_code == "artifact_planner_stale_or_unavailable"
    with pytest.raises(OperationError, match="planner_structure_stale"):
        await service.inputs(t.owner, t.workspace, run.id, uuid4())
    with pytest.raises(DBAPIError, match="planner_history_requires_restore_plan"):
        await asyncio.to_thread(migration.downgrade, Config("alembic.ini"), "0015_copy_adoption")
    async with t.admin.transaction() as s:
        assert await s.scalar(text("SELECT version_num FROM alembic_version")) == "0016_planner"
        assert await s.scalar(select(func.count()).select_from(AIArtifact)) == 1
        stored = await s.scalar(select(AIInput).where(AIInput.run_id == run.id))
        assert stored and stored.planner_context == inputs.planner_context.model_dump(mode="json")


@pytest.mark.parametrize(
    "change", ["plan", "campaign", "policy", "expiry", "cancel", "fact_draft", "fact_confirmed"]
)
async def test_plan_current_queue_gates(
    tenants: TenantFixture,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    if change in {"plan", "campaign"}:
        await change_intent(p, cmd, change)
    elif change == "policy":
        await change_policy(p)
    elif change == "expiry":
        inputs = await service.inputs(t.owner, t.workspace, run.id, uuid4())
        assert inputs.planner_context and isinstance(
            inputs.planner_context.plan.body, c.ContentPlan
        )
        deadline = inputs.planner_context.plan.body.slots[0].planned_at
        monkeypatch.setattr("smm_gpt.services.planner.utcnow", lambda: deadline)
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
    else:
        created = await p.run(
            c.CreateRecord(
                body=p.fact.body,
                replaces_id=p.fact.id,
                expires_at=p.fact.expires_at,
                idempotency_key=uuid4().hex,
            )
        )
        if change == "fact_confirmed":
            row = await p.core.read_record(t.owner, t.workspace, created.entity_id, uuid4())
            await p.run(
                c.ConfirmRecord(
                    record_id=row.id,
                    content_hash=row.content_hash,
                    confirmed=True,
                    idempotency_key=uuid4().hex,
                )
            )
    planner = PlanGateway()
    dispatched = await process(
        t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, planning_gateway=planner
    )
    assert dispatched == (change == "fact_draft")
    assert planner.calls == (1 if change == "fact_draft" else 0)


@pytest.mark.parametrize("change", ["campaign", "policy", "cancel", "profile"])
async def test_plan_inflight_changes_discard_output(tenants: TenantFixture, change: str) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    planner = PlanGateway(pause=True)
    task = asyncio.create_task(
        process(
            t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, planning_gateway=planner
        )
    )
    await asyncio.wait_for(planner.entered.wait(), 5)
    try:
        if change == "campaign":
            await change_intent(p, cmd, "campaign")
        elif change == "policy":
            await change_policy(p)
        elif change == "profile":
            from smm_gpt.domain.profiles import SelectTesting
            from smm_gpt.services.profiles import ProfileService

            profiles = ProfileService(t.access)
            head = await profiles.read(t.owner, t.workspace, "content_planner", uuid4())
            await profiles.execute(
                t.owner,
                t.workspace,
                SelectTesting(
                    idempotency_key=uuid4().hex,
                    profile="content_planner",
                    expected_revision=head.revision,
                    version_id=head.latest.id,
                    content_hash=head.latest.content_hash,
                    reason="New explicit testing selection",
                    human_confirmed=True,
                ),
                uuid4(),
            )
        else:
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
    finally:
        planner.release.set()
    assert not await task
    result = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert result.state == ("cancelled" if change == "cancel" else "failed")
    assert result.usage["input_tokens"] == 20 and result.plan_draft is None
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(AIArtifact)) == 0


@pytest.mark.parametrize("outcome", ["invalid", "unknown"])
async def test_plan_never_retries_unknown_or_invalid(tenants: TenantFixture, outcome: str) -> None:
    t = tenants
    cfg, service, cmd, _ = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    planner = PlanGateway(outcome=outcome)
    for _ in range(2):
        assert not await process(
            t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, planning_gateway=planner
        )
    result = await service.start(t.owner, t.workspace, cmd, uuid4())
    assert result.state == ("failed" if outcome == "invalid" else "unknown")
    assert result.plan_draft is None and planner.calls == 1


async def test_plan_permissions_immutability_and_typed_gates(tenants: TenantFixture) -> None:
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
        (t.worker, "INSERT INTO content_records SELECT * FROM content_records"),
        (t.worker, "INSERT INTO content_decisions SELECT * FROM content_decisions"),
        (t.admin, "UPDATE ai_inputs SET planner_context=NULL"),
        (t.admin, "DELETE FROM ai_inputs"),
    ]:
        with pytest.raises(DBAPIError):
            async with database.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(sql))
    legacy = RunAssessment(
        idempotency_key=uuid4().hex,
        profile="content_planner",
        brand_id=p.brand,
        question="Untyped request",
        testing_only=True,
    )
    assert (
        await service.start(t.owner, t.workspace, legacy, uuid4())
    ).error_code == "planner_plan_request_required"
    for field in ["plan_id", "brand_id", "fact_ids"]:
        bad = cmd.model_copy(
            update={
                "idempotency_key": uuid4().hex,
                field: [uuid4()] if field == "fact_ids" else uuid4(),
            }
        )
        if field == "brand_id":
            with pytest.raises(OperationError):
                await service.start(t.owner, t.workspace, bad, uuid4())
        else:
            assert (await service.start(t.owner, t.workspace, bad, uuid4())).state == "blocked"


async def test_plan_owner_revocation_and_worker_boolean_boundary(tenants: TenantFixture) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    plan = await p.core.read_record(t.owner, t.workspace, cmd.plan_id, uuid4())
    assert isinstance(plan.body, c.ContentPlan)
    campaign = await p.core.read_record(t.owner, t.workspace, plan.body.campaign_id, uuid4())
    created = await p.run(
        c.CreateRecord(
            body=campaign.body.model_copy(update={"owner_id": t.viewer.user_id}),
            replaces_id=campaign.id,
            expires_at=campaign.expires_at,
            idempotency_key=uuid4().hex,
        )
    )
    changed = await p.run(
        c.CreateRecord(
            body=plan.body.model_copy(update={"campaign_id": created.entity_id}),
            replaces_id=plan.id,
            expires_at=plan.expires_at,
            idempotency_key=uuid4().hex,
        )
    )
    plan = await p.core.read_record(t.owner, t.workspace, changed.entity_id, uuid4())
    cmd = cmd.model_copy(update={"plan_id": plan.id, "content_hash": plan.content_hash})
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    assert run.state == "queued"
    async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
        assert await s.scalar(select(func.smm_assignable_member(t.workspace, t.viewer.user_id)))
        assert not await s.scalar(
            select(func.smm_assignable_member(t.other_workspace, t.other.user_id))
        )
    async with t.admin.transaction() as s:
        await s.execute(
            update(Membership).where(Membership.user_id == t.viewer.user_id).values(active=False)
        )
    planner = PlanGateway()
    assert not await process(
        t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, planning_gateway=planner
    )
    assert planner.calls == 0
    assert (
        await service.read(t.owner, t.workspace, run.id, uuid4())
    ).error_code == "assignee_unavailable"


async def test_plan_db_guard_cannot_cross_bind_or_run_stale_intent(tenants: TenantFixture) -> None:
    t = tenants
    _, service, cmd, p = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    async with t.admin.transaction() as s:
        row = await s.scalar(select(AIInput).where(AIInput.run_id == run.id))
        assert row
        original = {col.name: getattr(row, col.name) for col in AIInput.__table__.columns}
    for change in ["missing", "plan", "brand"]:
        values = {**original, "id": uuid4()}
        if change == "missing":
            values["planner_context"] = None
        elif change == "plan":
            values["plan_id"] = p.brief.id
        else:
            inputs = await service.inputs(t.owner, t.workspace, run.id, uuid4())
            assert inputs.planner_context
            inputs.planner_context.brand_id = uuid4()
            values["planner_context"] = inputs.planner_context.model_dump(mode="json")
        with pytest.raises(DBAPIError, match="planner_input_required"):
            async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
                s.add(AIInput(**values))
    await change_intent(p, cmd, "plan")
    with pytest.raises(DBAPIError, match="planner_current_input_required"):
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


@pytest.mark.parametrize("change", ["too_many", "duplicate", "evidence_horizon"])
async def test_plan_rejects_unbounded_slots_and_evidence_expiring_before_slot(
    tenants: TenantFixture,
    change: str,
) -> None:
    t = tenants
    _, service, cmd, p = await prepare(t)
    plan = await p.core.read_record(t.owner, t.workspace, cmd.plan_id, uuid4())
    assert isinstance(plan.body, c.ContentPlan)
    slot = plan.body.slots[0]
    if change == "evidence_horizon":
        created = await p.run(
            c.CreateRecord(
                body=p.fact.body,
                replaces_id=p.fact.id,
                expires_at=slot.planned_at - timedelta(minutes=1),
                idempotency_key=uuid4().hex,
            )
        )
        fact = await p.core.read_record(t.owner, t.workspace, created.entity_id, uuid4())
        confirmed = await p.run(
            c.ConfirmRecord(
                record_id=fact.id,
                content_hash=fact.content_hash,
                confirmed=True,
                idempotency_key=uuid4().hex,
            )
        )
        cmd.fact_ids = [confirmed.entity_id]
    else:
        slots = (
            [slot, slot]
            if change == "duplicate"
            else [
                slot.model_copy(update={"planned_at": slot.planned_at + timedelta(minutes=i)})
                for i in range(6)
            ]
        )
        created = await p.run(
            c.CreateRecord(
                body=plan.body.model_copy(update={"slots": slots}),
                replaces_id=plan.id,
                expires_at=plan.expires_at,
                idempotency_key=uuid4().hex,
            )
        )
        plan = await p.core.read_record(t.owner, t.workspace, created.entity_id, uuid4())
        cmd.plan_id, cmd.content_hash = plan.id, plan.content_hash
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    assert run.state == "blocked"
    assert (
        run.error_code
        == {
            "too_many": "planner_slot_limit_exceeded",
            "duplicate": "planner_context_invalid",
            "evidence_horizon": "planner_evidence_stale",
        }[change]
    )
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(AIInput)) == 0
