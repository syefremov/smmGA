import asyncio
from uuid import uuid4

import pytest
from alembic import command as migration
from alembic.config import Config
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from smm_gpt.domain.access import AccessDenied, Principal
from smm_gpt.domain.ai import CancelAssessment, Profile
from smm_gpt.domain.knowledge import Citation
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIRun
from smm_gpt.infrastructure.cost_models import AICostObservation, AICostReservation
from smm_gpt.infrastructure.models import Identity, Membership
from smm_gpt.services.ai_costs import CostService
from smm_gpt.services.model_gateway import GatewayResult
from smm_gpt.workers.ai import process

from .conftest import TenantFixture
from .test_ai_queue import Gateway, prepare

pytestmark = pytest.mark.integration


async def test_budget_serializes_cross_actor_admission_and_private_history(
    tenants: TenantFixture,
) -> None:
    t = tenants
    settings, ai, c, _ = await prepare(t)
    assert settings.ai_cost_policy
    settings.ai_cost_policy = settings.ai_cost_policy.model_copy(
        update={"workspace_limit_microusd": 20_000}
    )
    async with t.admin.transaction() as s:
        s.add(Membership(user_id=t.other.user_id, workspace_id=t.workspace, role="owner"))
    actors = [t.owner, t.other, t.owner]
    results = await asyncio.gather(
        *[
            ai.start(
                actor, t.workspace, c.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
            )
            for actor in actors
        ]
    )
    assert sorted(r.state for r in results) == ["blocked", "queued", "queued"]
    costs = CostService(t.access, settings)
    summary = await costs.summary(t.owner, t.workspace, uuid4())
    assert summary.reserved_microusd == 20_000 and summary.available_microusd == 0
    assert summary.estimated_microusd == summary.unresolved_runs == 0
    assert await costs.summary(t.other, t.workspace, uuid4()) == summary
    for actor, result in zip(actors, results, strict=True):
        if result.state == "queued":
            receipt = await costs.receipt(actor, t.workspace, result.id, uuid4())
            assert receipt.observation is None and receipt.reserved_microusd == 10_000
            other = t.other if actor == t.owner else t.owner
            with pytest.raises(OperationError, match="not_found"):
                await costs.receipt(other, t.workspace, result.id, uuid4())
    for actor in (t.viewer, Principal(t.owner.user_id, t.owner.identity_id, False)):
        with pytest.raises(AccessDenied):
            await costs.summary(actor, t.workspace, uuid4())
    with pytest.raises(AccessDenied):
        await costs.summary(t.owner, t.other_workspace, uuid4())
    async with t.runtime.transaction(t.other.user_id, t.other_workspace) as s:
        assert await s.scalar(select(AICostReservation.id)) is None
        with pytest.raises(DBAPIError, match="access_denied"):
            await s.execute(text("SELECT * FROM smm_ai_cost_totals(:wid)"), {"wid": t.workspace})


async def test_cost_ledger_once_immutable_and_no_automatic_refund(tenants: TenantFixture) -> None:
    t = tenants
    settings, ai, c, _ = await prepare(t)
    costs = CostService(t.access, settings)
    run = await ai.start(t.owner, t.workspace, c, uuid4())
    assert (await ai.start(t.owner, t.workspace, c, uuid4())).id == run.id
    gateway = Gateway()
    assert await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    assert not await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    receipt = await costs.receipt(t.owner, t.workspace, run.id, uuid4())
    assert receipt.observation and receipt.observation.estimated_microusd == 120
    assert receipt.observation.input_tokens == 20 and receipt.observation.output_tokens == 10
    assert "test-only" not in receipt.model_dump_json()
    for db in (t.runtime, t.worker, t.admin):
        for table in ("ai_cost_reservations", "ai_cost_observations"):
            with pytest.raises(DBAPIError):
                async with db.transaction(t.owner.user_id, t.workspace) as s:
                    await s.execute(text(f"DELETE FROM {table}"))
    for privilege, table, db in (
        ("INSERT", "ai_cost_observations", t.runtime),
        ("INSERT", "ai_cost_reservations", t.worker),
    ):
        async with db.transaction(t.owner.user_id, t.workspace) as s:
            assert not await s.scalar(
                text("SELECT has_table_privilege(current_user,:table,:priv)"),
                {"table": table, "priv": privilege},
            )
    cancelled = await ai.start(
        t.owner, t.workspace, c.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    await ai.cancel(
        t.owner,
        t.workspace,
        cancelled.id,
        CancelAssessment(idempotency_key=uuid4().hex, expected_version=1),
        uuid4(),
    )
    summary = await costs.summary(t.owner, t.workspace, uuid4())
    assert summary.reserved_microusd == 20_000 and summary.estimated_microusd == 120
    assert summary.unresolved_runs == summary.overrun_runs == 0
    with pytest.raises(DBAPIError, match="ai_cost_history_requires_restore_plan"):
        await asyncio.to_thread(migration.downgrade, Config("alembic.ini"), "0018_text_files")
    assert await costs.receipt(t.owner, t.workspace, run.id, uuid4()) == receipt


@pytest.mark.parametrize("failure", ["unknown", "overrun", "model_mismatch"])
async def test_uncertain_spend_and_overrun_freeze_new_requests(
    tenants: TenantFixture, failure: str
) -> None:
    t = tenants
    settings, ai, c, _ = await prepare(t)
    costs = CostService(t.access, settings)
    run = await ai.start(t.owner, t.workspace, c, uuid4())

    class FailingGateway(Gateway):
        async def assess(
            self, profile: Profile, question: str, citations: list[Citation]
        ) -> GatewayResult:
            result = await super().assess(profile, question, citations)
            if failure == "unknown":
                raise OperationError("model_outcome_unknown")
            if failure == "model_mismatch":
                return result.model_copy(update={"model": "unpriced-model"})
            return result.model_copy(update={"output_tokens": 2000})

    gateway = FailingGateway()
    await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    summary = await costs.summary(t.owner, t.workspace, uuid4())
    assert summary.reserved_microusd == 10_000
    assert summary.overrun_runs == (1 if failure == "overrun" else 0)
    assert summary.unresolved_runs == (0 if failure == "overrun" else 1)
    next_run = await ai.start(
        t.owner, t.workspace, c.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    assert next_run.state == "blocked" and next_run.error_code == "ai_cost_reconciliation_required"
    assert not await process(t.worker, settings, gateway, t.workspace, next_run.id, t.owner.user_id)
    assert gateway.calls == 1
    assert (await ai.start(t.owner, t.workspace, c, uuid4())).id == run.id
    assert (await costs.summary(t.owner, t.workspace, uuid4())).reserved_microusd == 10_000


async def test_policy_fence_and_missing_policy_never_dispatch(tenants: TenantFixture) -> None:
    t = tenants
    settings, ai, c, _ = await prepare(t)
    run = await ai.start(t.owner, t.workspace, c, uuid4())
    # Simulate a legacy queued row with no cost ledger, without altering immutable history.
    orphan_id = uuid4()
    async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
        original = await s.scalar(select(AIRun).where(AIRun.id == run.id))
        assert original
        values = {column.key: getattr(original, column.key) for column in AIRun.__table__.columns}
        s.add(AIRun(**{**values, "id": orphan_id, "key_hash": uuid4().hex * 2}))
    with pytest.raises(DBAPIError, match="ai_cost_reservation_required"):
        async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
            await s.execute(
                text("UPDATE ai_runs SET state='running',version=version+1 WHERE id=:id"),
                {"id": orphan_id},
            )
    assert settings.ai_cost_policy
    settings.ai_cost_policy = settings.ai_cost_policy.model_copy(update={"version": "synthetic-v2"})
    gateway = Gateway()
    assert not await process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    assert (
        await ai.read(t.owner, t.workspace, run.id, uuid4())
    ).error_code == "ai_cost_policy_changed"
    settings.ai_cost_policy = None
    blocked = await ai.start(
        t.owner, t.workspace, c.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    assert blocked.state == "blocked" and blocked.error_code == "ai_budget_not_configured"
    assert gateway.calls == 0


async def test_inflight_wait_then_snapshot_cost_survives_cancel_and_policy_change(
    tenants: TenantFixture,
) -> None:
    t = tenants
    settings, ai, c, _ = await prepare(t)
    first = await ai.start(t.owner, t.workspace, c, uuid4())
    second = await ai.start(
        t.owner, t.workspace, c.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    gateway = Gateway(pause=True)
    pending = asyncio.create_task(
        process(t.worker, settings, gateway, t.workspace, first.id, t.owner.user_id)
    )
    await asyncio.wait_for(gateway.entered.wait(), 5)
    try:
        async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
            lease = await s.scalar(select(AIRun.lease_id).where(AIRun.id == first.id))
        for lease_id, amount, code in (
            (uuid4(), 120, "cost_observation_fenced"),
            (lease, 1, "cost_estimate_mismatch"),
        ):
            with pytest.raises(DBAPIError, match=code):
                async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
                    s.add(
                        AICostObservation(
                            workspace_id=t.workspace,
                            run_id=first.id,
                            actor_id=t.owner.user_id,
                            lease_id=lease_id,
                            model="synthetic-model",
                            response_id="synthetic-response",
                            input_tokens=20,
                            output_tokens=10,
                            estimated_microusd=amount,
                        )
                    )
                    await s.flush()
        assert not await process(
            t.worker, settings, Gateway(), t.workspace, second.id, t.owner.user_id
        )
        assert (await ai.read(t.owner, t.workspace, second.id, uuid4())).state == "queued"
        await ai.cancel(
            t.owner,
            t.workspace,
            first.id,
            CancelAssessment(idempotency_key=uuid4().hex, expected_version=2),
            uuid4(),
        )
        assert settings.ai_cost_policy
        settings.ai_cost_policy = settings.ai_cost_policy.model_copy(
            update={"input_rate_microusd_per_million": 9_000_000}
        )
    finally:
        gateway.release.set()
    assert not await pending
    receipt = await CostService(t.access, settings).receipt(t.owner, t.workspace, first.id, uuid4())
    assert receipt.observation and receipt.observation.estimated_microusd == 120
    assert receipt.policy.input_rate_microusd_per_million == 2_000_000
    assert (await ai.read(t.owner, t.workspace, first.id, uuid4())).state == "cancelled"


async def test_revoked_identity_cannot_finalize_cost_observation(tenants: TenantFixture) -> None:
    t = tenants
    settings, ai, c, _ = await prepare(t)
    run = await ai.start(t.owner, t.workspace, c, uuid4())
    gateway = Gateway(pause=True)
    pending = asyncio.create_task(
        process(t.worker, settings, gateway, t.workspace, run.id, t.owner.user_id)
    )
    await asyncio.wait_for(gateway.entered.wait(), 5)
    try:
        async with t.admin.transaction() as s:
            await s.execute(
                update(Identity).where(Identity.id == t.owner.identity_id).values(active=False)
            )
    finally:
        gateway.release.set()
    assert not await pending
    async with t.admin.transaction() as s:
        assert await s.scalar(select(AICostObservation.id)) is None
        assert await s.scalar(select(AICostReservation.id)) is not None
        # Restore only this synthetic identity to inspect the frozen historical exposure.
        await s.execute(
            update(Identity).where(Identity.id == t.owner.identity_id).values(active=True)
        )
    summary = await CostService(t.access, settings).summary(t.owner, t.workspace, uuid4())
    assert summary.unresolved_runs == 1 and summary.reserved_microusd == 10_000
    assert gateway.calls == 1
