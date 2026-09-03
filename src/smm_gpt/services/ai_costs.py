"""Budget admission is SQL-based, serialized with the existing workspace knowledge lock."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.core.config import Settings
from smm_gpt.domain import ai_costs as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIRun
from smm_gpt.infrastructure.cost_models import AICostObservation, AICostReservation
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import AccessService


def estimate(policy: d.CostPolicy, input_tokens: int, output_tokens: int) -> int:
    if any(
        type(n) is not int or not 0 <= n <= 1_000_000_000 for n in (input_tokens, output_tokens)
    ):
        raise OperationError("model_usage_invalid")
    numerator = (
        input_tokens * policy.input_rate_microusd_per_million
        + output_tokens * policy.output_rate_microusd_per_million
    )
    return (numerator + 999_999) // 1_000_000


def configured(settings: Settings) -> d.CostPolicy:
    policy = settings.ai_cost_policy
    if policy is None:
        raise OperationError("ai_budget_not_configured")
    if policy.model != settings.ai_model or policy.valid_until <= utcnow():
        raise OperationError("ai_price_policy_unavailable")
    return policy


async def totals(s: AsyncSession, wid: UUID) -> dict[str, int]:
    row = (
        (await s.execute(text("SELECT * FROM public.smm_ai_cost_totals(:wid)"), {"wid": wid}))
        .mappings()
        .one()
    )
    return {str(key): int(value) for key, value in row.items()}


async def admission(s: AsyncSession, settings: Settings, wid: UUID) -> d.CostPolicy:
    policy = configured(settings)
    state = await totals(s, wid)
    if state["unresolved_runs"] or state["overrun_runs"]:
        raise OperationError("ai_cost_reconciliation_required")
    if state["reserved_microusd"] + policy.reserve_microusd > policy.workspace_limit_microusd:
        raise OperationError("ai_budget_exhausted")
    return policy


async def dispatch(s: AsyncSession, settings: Settings, run: AIRun, input_hash: str) -> bool:
    policy = configured(settings)
    row = await s.scalar(
        select(AICostReservation).where(
            AICostReservation.workspace_id == run.workspace_id, AICostReservation.run_id == run.id
        )
    )
    if row is None:
        raise OperationError("ai_cost_reservation_required")
    if (
        row.input_hash != input_hash
        or row.policy_hash != canonical_hash(policy.model_dump(mode="json"))
        or canonical_hash(row.policy) != row.policy_hash
    ):
        raise OperationError("ai_cost_policy_changed")
    state = await totals(s, run.workspace_id)
    if state["unresolved_runs"] or state["overrun_runs"]:
        raise OperationError("ai_cost_reconciliation_required")
    if state["reserved_microusd"] > policy.workspace_limit_microusd:
        raise OperationError("ai_budget_exhausted")
    # Wait in queued while another run can still return accounting. Never parallelize unknown spend.
    return state["in_flight_runs"] == 0


async def observe(
    s: AsyncSession,
    run: AIRun,
    lease: UUID,
    model: str,
    response_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    row = await s.scalar(
        select(AICostReservation).where(
            AICostReservation.workspace_id == run.workspace_id, AICostReservation.run_id == run.id
        )
    )
    if row is None:
        raise OperationError("ai_cost_reservation_required")
    policy = d.CostPolicy.model_validate(row.policy)
    if model != policy.model:
        raise OperationError("model_pricing_mismatch")
    amount = estimate(policy, input_tokens, output_tokens)
    s.add(
        AICostObservation(
            workspace_id=run.workspace_id,
            run_id=run.id,
            actor_id=run.actor_id,
            lease_id=lease,
            model=model,
            response_id=response_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_microusd=amount,
        )
    )
    await s.flush()


class CostService:
    def __init__(self, access: AccessService, settings: Settings):
        self.access, self.settings = access, settings

    async def summary(self, actor: Principal, wid: UUID, request: UUID) -> d.CostSummary:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            state = await totals(s, wid)
            policy = self.settings.ai_cost_policy
            return d.CostSummary(
                policy=policy,
                **state,
                available_microusd=max(
                    0,
                    (policy.workspace_limit_microusd if policy else 0) - state["reserved_microusd"],
                ),
            )

    async def receipt(self, actor: Principal, wid: UUID, rid: UUID, request: UUID) -> d.CostReceipt:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            row = await s.scalar(
                select(AICostReservation).where(
                    AICostReservation.workspace_id == wid, AICostReservation.run_id == rid
                )
            )
            if row is None:
                raise OperationError("not_found", 404)
            observation = await s.scalar(
                select(AICostObservation).where(
                    AICostObservation.workspace_id == wid, AICostObservation.run_id == rid
                )
            )
            return d.CostReceipt(
                run_id=rid,
                created_at=row.created_at,
                input_hash=row.input_hash,
                policy=d.CostPolicy.model_validate(row.policy),
                policy_hash=row.policy_hash,
                reserved_microusd=row.reserved_microusd,
                observation=d.CostObservationView.model_validate(observation)
                if observation
                else None,
            )
