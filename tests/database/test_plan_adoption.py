import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command as migration
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from smm_gpt.domain import content as c
from smm_gpt.domain import plan_adoption as d
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.planner import PlanDraft, PlanningContext, RunPlanDraft
from smm_gpt.domain.profiles import SelectTesting
from smm_gpt.infrastructure.ai_models import PlanAdoption, PlanNotes
from smm_gpt.infrastructure.content_models import ContentLink, ContentRecord
from smm_gpt.infrastructure.models import Membership
from smm_gpt.services.ai import AIService
from smm_gpt.services.plan_adoption import PlanAdoptionService, candidate
from smm_gpt.services.plan_notes import PlanNotesService
from smm_gpt.services.profiles import ProfileService
from smm_gpt.workers.ai import process

from .conftest import TenantFixture
from .test_ai_queue import Gateway
from .test_content import Pilot
from .test_editor import change_policy
from .test_planner import PlanGateway, prepare

pytestmark = pytest.mark.integration


async def ready(
    t: TenantFixture,
) -> tuple[PlanAdoptionService, d.PlanAdoptionPreview, Pilot, AIService]:
    cfg, ai, cmd, p = await prepare(t)
    run = await ai.start(t.owner, t.workspace, cmd, uuid4())
    assert await process(
        t.worker,
        cfg,
        Gateway(),
        t.workspace,
        run.id,
        t.owner.user_id,
        planning_gateway=PlanGateway(),
    )
    service = PlanAdoptionService(t.access)
    return service, await service.preview(t.owner, t.workspace, run.id, uuid4()), p, ai


def command(preview: d.PlanAdoptionPreview) -> d.AdoptPlanDraft:
    return d.AdoptPlanDraft(
        idempotency_key=uuid4().hex,
        artifact_id=preview.artifact_id,
        artifact_hash=preview.artifact_hash,
        preview_hash=preview.preview_hash,
        proposed_content_hash=preview.proposed_content_hash,
        notes_hash=preview.notes_hash,
        expected_plan_number=preview.source_plan_number,
        reason="Private human transfer reason",
        human_confirmed=True,
        share_with_workspace_confirmed=True,
    )


async def test_plan_adoption_exact_shared_notes_private_receipt(tenants: TenantFixture) -> None:
    t = tenants
    service, preview, p, ai = await ready(t)
    notes = PlanNotesService(t.access)
    before = await p.post()
    original = await p.core.read_record(t.owner, t.workspace, preview.source_plan_id, uuid4())
    assert await service.preview(t.owner, t.workspace, preview.run_id, uuid4()) == preview
    assert await service.read(t.owner, t.workspace, preview.run_id, uuid4()) is None
    assert await notes.read(t.viewer, t.workspace, original.id, uuid4()) is None
    receipt = await service.adopt(t.owner, t.workspace, preview.run_id, command(preview), uuid4())
    plan = await p.core.read_record(t.viewer, t.workspace, receipt.plan_id, uuid4())
    assert plan.body == preview.body and plan.content_hash == preview.proposed_content_hash
    assert plan.family_id == original.family_id and plan.number == original.number + 1
    assert plan.confirmed_by is None and plan.expires_at == original.expires_at
    assert await p.core.read_record(t.owner, t.workspace, original.id, uuid4()) == original
    assert await p.post() == before
    shared = await notes.read(t.viewer, t.workspace, plan.id, uuid4())
    assert shared and shared.body == preview.notes and shared.exact_version
    assert shared.content_hash == receipt.notes_hash and shared.plan_hash == receipt.content_hash
    assert command(preview).reason not in shared.model_dump_json()
    result = await ai.read(t.owner, t.workspace, preview.run_id, uuid4())
    assert result.plan_draft is None and result.plan_adoption == receipt
    assert result.error_code == "artifact_planner_stale_or_unavailable"
    async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
        targets = (
            await s.scalars(select(ContentLink.target_id).where(ContentLink.record_id == plan.id))
        ).all()
        assert set(preview.notes.evidence_record_ids) <= set(targets)
    # A new manual version retains ancestral limitations, not new-text validation.
    next_plan = await p.run(
        c.CreateRecord(
            body=plan.body,
            replaces_id=plan.id,
            expires_at=plan.expires_at,
            idempotency_key=uuid4().hex,
        )
    )
    inherited = await notes.read(t.viewer, t.workspace, next_plan.entity_id, uuid4())
    assert inherited and inherited.id == shared.id and not inherited.exact_version
    assert inherited.requested_plan_id == next_plan.entity_id and inherited.plan_id == plan.id
    assert await service.read(t.owner, t.workspace, preview.run_id, uuid4()) == receipt


async def test_plan_adoption_atomic_concurrent_idempotency_and_downgrade(
    tenants: TenantFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = tenants
    service, preview, p, _ = await ready(t)
    cmd = command(preview)
    async with t.admin.transaction() as s:
        count = await s.scalar(select(func.count()).select_from(ContentRecord))

    def fail(*args: object) -> None:
        raise OperationError("synthetic_audit_failure")

    with monkeypatch.context() as patch:
        patch.setattr("smm_gpt.services.plan_adoption.audit", fail)
        with pytest.raises(OperationError, match="synthetic_audit_failure"):
            await service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4())
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(ContentRecord)) == count
        assert await s.scalar(select(func.count()).select_from(PlanNotes)) == 0
        assert await s.scalar(select(func.count()).select_from(PlanAdoption)) == 0
    receipts = await asyncio.gather(
        *[service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4()) for _ in range(3)]
    )
    assert receipts[0] == receipts[1] == receipts[2]
    await change_policy(p)
    assert await service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4()) == receipts[0]
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await service.adopt(
            t.owner,
            t.workspace,
            preview.run_id,
            cmd.model_copy(update={"reason": "Changed"}),
            uuid4(),
        )
    with pytest.raises(OperationError, match="plan_already_adopted"):
        await service.adopt(t.owner, t.workspace, preview.run_id, command(preview), uuid4())
    with pytest.raises(DBAPIError, match="plan_adoption_history_requires_restore_plan"):
        await asyncio.to_thread(migration.downgrade, Config("alembic.ini"), "0016_planner")
    async with t.admin.transaction() as s:
        assert (
            await s.scalar(text("SELECT version_num FROM alembic_version")) == "0017_plan_adoption"
        )
        assert await s.scalar(select(func.count()).select_from(PlanNotes)) == 1
        assert await s.scalar(select(func.count()).select_from(PlanAdoption)) == 1


async def test_plan_adoption_exact_binding_and_stale_plan(tenants: TenantFixture) -> None:
    t = tenants
    service, preview, p, _ = await ready(t)
    cmd = command(preview)
    for field, value in {
        "artifact_id": uuid4(),
        "artifact_hash": "0" * 64,
        "preview_hash": "0" * 64,
        "proposed_content_hash": "0" * 64,
        "notes_hash": "0" * 64,
        "expected_plan_number": 999,
    }.items():
        with pytest.raises(OperationError, match="plan_adoption_preview_changed"):
            await service.adopt(
                t.owner, t.workspace, preview.run_id, cmd.model_copy(update={field: value}), uuid4()
            )
    for field in ("human_confirmed", "share_with_workspace_confirmed"):
        with pytest.raises(OperationError, match="confirmation_required"):
            await service.adopt(
                t.owner, t.workspace, preview.run_id, cmd.model_copy(update={field: False}), uuid4()
            )
    await p.run(
        c.CreateRecord(
            body=preview.body,
            replaces_id=preview.source_plan_id,
            expires_at=preview.expires_at,
            idempotency_key=uuid4().hex,
        )
    )
    with pytest.raises(OperationError):
        await service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4())
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(PlanNotes)) == 0


async def test_plan_adoption_authorization_and_rls(tenants: TenantFixture) -> None:
    t = tenants
    service, preview, _, _ = await ready(t)
    cmd = command(preview)
    for actor in (t.viewer, t.other, replace(t.owner, mfa=False)):
        with pytest.raises(AccessDenied):
            await service.adopt(actor, t.workspace, preview.run_id, cmd, uuid4())
    async with t.admin.transaction() as s:
        s.add(Membership(user_id=t.other.user_id, workspace_id=t.workspace, role="owner"))
    with pytest.raises(OperationError, match="not_found"):
        await service.preview(t.other, t.workspace, preview.run_id, uuid4())
    receipt = await service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4())
    for actor in (t.viewer, t.other):
        async with t.runtime.transaction(actor.user_id, t.workspace) as s:
            assert await s.scalar(select(func.count()).select_from(PlanAdoption)) == 0
            assert await s.scalar(select(func.count()).select_from(PlanNotes)) == 1
    async with t.runtime.transaction(t.other.user_id, t.other_workspace) as s:
        assert await s.scalar(select(func.count()).select_from(PlanNotes)) == 0
    for table in ("plan_notes", "plan_adoptions"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(f"SELECT * FROM {table}"))
        for operation in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}", f"TRUNCATE {table}"):
            with pytest.raises(DBAPIError):
                async with t.admin.transaction() as s:
                    await s.execute(text(operation))
    async with t.admin.transaction() as s:
        await s.execute(
            update(Membership)
            .where(Membership.user_id == t.owner.user_id, Membership.workspace_id == t.workspace)
            .values(active=False)
        )
    with pytest.raises(AccessDenied):
        await service.read(t.owner, t.workspace, preview.run_id, uuid4())
    assert await PlanNotesService(t.access).read(t.viewer, t.workspace, receipt.plan_id, uuid4())


async def test_notes_cannot_commit_without_receipt_and_replanning_keeps_gaps(
    tenants: TenantFixture,
) -> None:
    t = tenants
    service, preview, p, ai = await ready(t)
    with pytest.raises(DBAPIError, match="plan_notes_receipt_required"):
        async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
            s.add(
                PlanNotes(
                    workspace_id=t.workspace,
                    plan_id=preview.source_plan_id,
                    plan_hash=preview.source_content_hash,
                    content_hash=preview.notes_hash,
                    actor_id=t.owner.user_id,
                    body=preview.notes.model_dump(mode="json"),
                )
            )
            await s.flush()
    receipt = await service.adopt(t.owner, t.workspace, preview.run_id, command(preview), uuid4())
    head = await ProfileService(t.access).read(t.owner, t.workspace, "content_planner", uuid4())
    assert head.testing
    cmd = RunPlanDraft(
        idempotency_key=uuid4().hex,
        brand_id=p.brand,
        plan_id=receipt.plan_id,
        content_hash=receipt.content_hash,
        fact_ids=preview.notes.fact_ids,
        direction="Follow up",
        knowledge_gaps=preview.notes.knowledge_gaps,
        profile_version_id=head.testing.id,
        profile_selection_id=head.testing_selection_id,
        testing_only=True,
    )
    assert cmd.plan_id == receipt.plan_id
    blocked = await ai.start(
        t.owner, t.workspace, cmd.model_copy(update={"knowledge_gaps": []}), uuid4()
    )
    assert blocked.state == "blocked" and blocked.error_code == "planner_inherited_gaps_required"
    queued = await ai.start(
        t.owner, t.workspace, cmd.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    assert queued.state == "queued"


@pytest.mark.parametrize("change", ["policy", "profile", "expiry"])
async def test_adoption_rechecks_current_context(
    tenants: TenantFixture,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = tenants
    service, preview, p, _ = await ready(t)
    if change == "policy":
        await change_policy(p)
    elif change == "profile":
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
                reason="Separate selection",
                human_confirmed=True,
            ),
            uuid4(),
        )
    else:
        monkeypatch.setattr(
            "smm_gpt.services.planner.utcnow", lambda: preview.expires_at + timedelta(days=1)
        )
    with pytest.raises(OperationError):
        await service.adopt(t.owner, t.workspace, preview.run_id, command(preview), uuid4())
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(PlanAdoption)) == 0
        assert await s.scalar(select(func.count()).select_from(PlanNotes)) == 0


async def test_database_binding_rejects_changed_disclosure(
    tenants: TenantFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = tenants
    service, preview, _, _ = await ready(t)

    def changed(
        draft: PlanDraft, context: PlanningContext
    ) -> tuple[c.ContentPlan, d.PlanNotesBody]:
        body, notes = candidate(draft, context)
        notes.knowledge_gaps = []
        return body, notes

    monkeypatch.setattr("smm_gpt.services.plan_adoption.candidate", changed)
    forged = await service.preview(t.owner, t.workspace, preview.run_id, uuid4())
    with pytest.raises(DBAPIError, match="plan_adoption_binding_invalid"):
        await service.adopt(t.owner, t.workspace, preview.run_id, command(forged), uuid4())
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(PlanNotes)) == 0


async def test_competing_proposals_cannot_rebase_on_each_other(tenants: TenantFixture) -> None:
    t = tenants
    cfg, ai, cmd, _ = await prepare(t)
    run1 = await ai.start(t.owner, t.workspace, cmd, uuid4())
    run2 = await ai.start(
        t.owner, t.workspace, cmd.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    for run in (run1, run2):
        assert await process(
            t.worker,
            cfg,
            Gateway(),
            t.workspace,
            run.id,
            t.owner.user_id,
            planning_gateway=PlanGateway(),
        )
    service = PlanAdoptionService(t.access)
    previews = [
        await service.preview(t.owner, t.workspace, run.id, uuid4()) for run in (run1, run2)
    ]
    results = await asyncio.gather(
        *[
            service.adopt(t.owner, t.workspace, view.run_id, command(view), uuid4())
            for view in previews
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, d.PlanAdoptionView) for result in results) == 1
    assert sum(isinstance(result, OperationError) for result in results) == 1
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(PlanNotes)) == 1
