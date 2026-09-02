import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from smm_gpt.core.config import Settings
from smm_gpt.domain.ai import CancelAssessment, Profile, ReferenceAssessment, RunAssessment
from smm_gpt.domain.knowledge import ArchiveDocument, Citation
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIArtifact, AIInput, AIRun
from smm_gpt.infrastructure.models import Identity, Membership, utcnow
from smm_gpt.services.ai import AIService
from smm_gpt.services.knowledge import KnowledgeService
from smm_gpt.services.model_gateway import GatewayResult
from smm_gpt.workers.ai import process, reconcile

from .conftest import TenantFixture
from .test_knowledge import activate, seed, submit

pytestmark = pytest.mark.integration


class Gateway:
    def __init__(self, pause: bool = False):
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not pause:
            self.release.set()

    async def assess(
        self, profile: Profile, question: str, citations: list[Citation]
    ) -> GatewayResult:
        self.calls += 1
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), 5)
        return GatewayResult(
            assessment=ReferenceAssessment(
                statements=[], hypotheses=["Candidate only"], knowledge_gaps=[]
            ),
            model="synthetic-model",
            response_id="synthetic-response",
            input_tokens=20,
            output_tokens=10,
        )


def config(wid: UUID) -> Settings:
    return Settings(
        _env_file=None,
        ai_provider="openai",
        ai_model="synthetic-model",
        ai_api_key="test-only",
        ai_allowed_workspaces=(wid,),
        ai_worker_enabled=True,
    )


def command(bid: UUID) -> RunAssessment:
    return RunAssessment(
        idempotency_key=uuid4().hex,
        profile="product_expert",
        brand_id=bid,
        question="крем",
        testing_only=True,
    )


async def prepare(t: TenantFixture) -> tuple[Settings, AIService, RunAssessment, UUID]:
    bid = await seed(t)
    doc, _ = await submit(t, bid)
    await activate(t, doc)
    settings = config(t.workspace)
    return settings, AIService(t.access, settings), command(bid), doc.entity_id


async def test_queue_provenance_once_and_restricted_roles(tenants: TenantFixture) -> None:
    t = tenants
    settings, service, c, _ = await prepare(t)
    results = await asyncio.gather(
        *(service.start(t.owner, t.workspace, c, uuid4()) for _ in range(3))
    )
    assert len({r.id for r in results}) == 1 and all(r.state == "queued" for r in results)
    run = results[0]
    inputs = await service.inputs(t.owner, t.workspace, run.id, uuid4())
    assert inputs.question == "крем" and inputs.citations
    assert inputs.payload["store"] is False and inputs.payload["background"] is False
    assert "tools" not in inputs.payload and "test-only" not in inputs.model_dump_json()
    gateway = Gateway()
    disabled = settings.model_copy(update={"ai_worker_enabled": False})
    assert not await process(t.worker, disabled, gateway, t.workspace, run.id, t.owner.user_id)
    async with t.worker.transaction() as s:
        pending = (await s.execute(text("SELECT * FROM smm_ai_pending()"))).one()
        assert pending.run_id == run.id
    outcomes = await asyncio.gather(
        *(
            process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
            for _ in range(3)
        )
    )
    assert sum(outcomes) == gateway.calls == 1
    finished = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert finished.state == "needs_review" and finished.version == 3
    assert finished.started_at and finished.finished_at and finished.assessment
    assert finished.usage["attempts"] == 1 and finished.usage["cost_usd"] is None
    assert (
        await service.inputs(t.owner, t.workspace, run.id, uuid4())
    ).content_hash == inputs.content_hash
    # A second Owner in the same workspace still cannot read the initiating actor's inputs.
    async with t.admin.transaction() as s:
        s.add(Membership(workspace_id=t.workspace, user_id=t.other.user_id, role="owner"))
    with pytest.raises(OperationError, match="not_found"):
        await service.inputs(t.other, t.workspace, run.id, uuid4())
    async with t.runtime.transaction(t.other.user_id, t.workspace) as s:
        assert await s.scalar(select(AIInput.id)) is None
    async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
        assert (
            await s.scalar(text("SELECT has_table_privilege(current_user,'ai_artifacts','INSERT')"))
            is False
        )
    for statement in (
        "SELECT smm_ai_reconcile()",
        "SELECT * FROM smm_ai_pending()",
        "UPDATE ai_runs SET usage='{}'",
        "UPDATE ai_inputs SET question='changed'",
    ):
        with pytest.raises(DBAPIError):
            async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(statement))
    for statement in (
        "UPDATE knowledge_documents SET archived=true",
        "DELETE FROM ai_runs",
        "UPDATE ai_inputs SET question='changed'",
        "INSERT INTO ai_cancel_receipts DEFAULT VALUES",
    ):
        with pytest.raises(DBAPIError):
            async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(statement))
    with pytest.raises(DBAPIError):
        async with t.admin.transaction() as s:
            await s.execute(text("UPDATE ai_runs SET state='queued', version=version+1"))


async def test_cancel_before_and_during_dispatch(tenants: TenantFixture) -> None:
    t = tenants
    settings, service, c, _ = await prepare(t)
    queued = await service.start(t.owner, t.workspace, c, uuid4())
    cancel = CancelAssessment(idempotency_key=uuid4().hex, expected_version=1)
    receipt = await service.cancel(t.owner, t.workspace, queued.id, cancel, uuid4())
    assert receipt.state == "cancelled" and receipt.version == 2
    assert await service.cancel(t.owner, t.workspace, queued.id, cancel, uuid4()) == receipt
    gateway = Gateway()
    assert not await process(t.worker, settings, gateway, t.workspace, queued.id, t.owner.user_id)
    assert gateway.calls == 0
    run = await service.start(
        t.owner, t.workspace, c.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    paused = Gateway(pause=True)
    task = asyncio.create_task(
        process(t.worker, settings, paused, t.workspace, run.id, t.owner.user_id)
    )
    await asyncio.wait_for(paused.entered.wait(), 5)
    try:
        with pytest.raises(OperationError, match="run_conflict"):
            await service.cancel(
                t.owner,
                t.workspace,
                run.id,
                CancelAssessment(idempotency_key=uuid4().hex, expected_version=1),
                uuid4(),
            )
        receipt = await service.cancel(
            t.owner,
            t.workspace,
            run.id,
            CancelAssessment(idempotency_key=uuid4().hex, expected_version=2),
            uuid4(),
        )
        assert receipt.state == "cancel_requested"
    finally:
        paused.release.set()
    assert not await task
    ended = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert ended.state == "cancelled" and ended.assessment is None
    assert ended.usage["input_tokens"] == 20 and ended.usage["cost_usd"] is None
    assert not await process(t.worker, settings, paused, t.workspace, run.id, t.owner.user_id)
    assert paused.calls == 1


async def test_expired_dispatch_is_unknown_and_late_result_fenced(
    tenants: TenantFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = tenants
    settings, service, c, _ = await prepare(t)
    run = await service.start(t.owner, t.workspace, c, uuid4())
    old_clock = utcnow() - timedelta(minutes=3)
    monkeypatch.setattr("smm_gpt.workers.ai.utcnow", lambda: old_clock)
    gateway = Gateway(pause=True)
    task = asyncio.create_task(
        process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    )
    await asyncio.wait_for(gateway.entered.wait(), 5)
    try:
        assert await reconcile(t.worker) == 1
        assert await reconcile(t.worker) == 0
    finally:
        gateway.release.set()
    assert not await task
    ended = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert ended.state == "unknown" and ended.error_code == "interrupted_run_not_replayed"
    assert ended.usage["attempts"] == 1 and ended.assessment is None
    assert (await service.start(t.owner, t.workspace, c, uuid4())).id == run.id
    assert not await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
        assert await s.scalar(select(AIArtifact.id)) is None
    assert gateway.calls == 1


async def test_stale_inputs_and_changed_configuration_block_dispatch(
    tenants: TenantFixture,
) -> None:
    t = tenants
    settings, service, c, doc = await prepare(t)
    run = await service.start(t.owner, t.workspace, c, uuid4())
    gateway = Gateway()
    changed = settings.model_copy(update={"ai_model": "different-model"})
    assert not await process(t.worker, changed, gateway, t.workspace, run.id, t.owner.user_id)
    assert (
        await service.read(t.owner, t.workspace, run.id, uuid4())
    ).error_code == "model_configuration_changed"
    run = await service.start(
        t.owner, t.workspace, c.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    await KnowledgeService(t.access).execute(
        t.owner,
        t.workspace,
        ArchiveDocument(idempotency_key=uuid4().hex, document_id=doc, expected_version=2),
        uuid4(),
    )
    assert not await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    assert gateway.calls == 0
    with pytest.raises(OperationError, match="source_unavailable"):
        await service.inputs(t.owner, t.workspace, run.id, uuid4())


async def test_revoked_identity_reconciliation_and_quota(tenants: TenantFixture) -> None:
    t = tenants
    settings, service, c, _ = await prepare(t)
    settings.ai_daily_run_limit = 1
    run = await service.start(t.owner, t.workspace, c, uuid4())
    with pytest.raises(OperationError, match="ai_run_quota_exceeded"):
        await service.start(
            t.owner, t.workspace, c.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
        )
    async with t.admin.transaction() as s:
        identity = await s.get(Identity, t.owner.identity_id)
        assert identity
        identity.active = False
    assert await reconcile(t.worker) == 1
    async with t.admin.transaction() as s:
        row = await s.get(AIRun, run.id)
        assert row and row.state == "blocked" and row.error_code == "authorization_changed"
        assert row.usage["attempts"] == 0


async def test_source_change_during_call_discards_result(tenants: TenantFixture) -> None:
    t = tenants
    settings, service, c, doc = await prepare(t)
    run = await service.start(t.owner, t.workspace, c, uuid4())
    gateway = Gateway(pause=True)
    task = asyncio.create_task(
        process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    )
    await asyncio.wait_for(gateway.entered.wait(), 5)
    try:
        await KnowledgeService(t.access).execute(
            t.owner,
            t.workspace,
            ArchiveDocument(idempotency_key=uuid4().hex, document_id=doc, expected_version=2),
            uuid4(),
        )
    finally:
        gateway.release.set()
    assert not await task
    ended = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert ended.state == "failed" and ended.error_code == "source_unavailable"
    assert ended.assessment is None and ended.usage["input_tokens"] == 20
    async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
        assert await s.scalar(select(AIArtifact.id)) is None


async def test_legacy_interrupted_run_never_dispatched(tenants: TenantFixture) -> None:
    t = tenants
    bid = await seed(t)
    rid = uuid4()
    async with t.admin.transaction() as s:
        s.add(
            AIRun(
                id=rid,
                workspace_id=t.workspace,
                actor_id=t.owner.user_id,
                brand_id=bid,
                key_hash="a" * 64,
                request_hash="b" * 64,
                profile="product_expert",
                profile_version="reference-assessment-v1",
                profile_snapshot={},
                state="running",
                provider="openai",
                model="synthetic-model",
                usage={"attempts": 1},
                created_at=utcnow() - timedelta(minutes=3),
            )
        )
    assert await reconcile(t.worker) == 1
    assert (
        await AIService(t.access, config(t.workspace)).read(t.owner, t.workspace, rid, uuid4())
    ).state == "unknown"
    gateway = Gateway()
    assert not await process(
        t.worker, config(t.workspace), gateway, t.workspace, rid, t.owner.user_id
    )
    assert gateway.calls == 0


@pytest.mark.parametrize("field", ["model", "response_id"])
async def test_unsafe_response_metadata_never_persisted(tenants: TenantFixture, field: str) -> None:
    t = tenants
    settings, service, c, _ = await prepare(t)
    run = await service.start(t.owner, t.workspace, c, uuid4())

    class UnsafeGateway(Gateway):
        async def assess(
            self, profile: Profile, question: str, citations: list[Citation]
        ) -> GatewayResult:
            result = await super().assess(profile, question, citations)
            return result.model_copy(update={field: "Bearer synthetic-rejected-metadata"})

    gateway = UnsafeGateway()
    assert not await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    ended = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert ended.state == "failed" and ended.error_code == "unsafe_or_oversized_text"
    assert ended.assessment is None and "synthetic-rejected-metadata" not in ended.model_dump_json()
    assert ended.usage["attempts"] == 1 and ended.usage["cost_status"] == "unknown"
    assert not await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    assert gateway.calls == 1
