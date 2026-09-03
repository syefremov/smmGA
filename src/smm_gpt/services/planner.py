"""Read-only current SQL planning context and deterministic proposal bindings."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import content as c
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.planner import PlanDraft, PlanningContext
from smm_gpt.infrastructure.content_models import ContentRecord
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.content_records import current, member, record, workspace_lock
from smm_gpt.services.knowledge_text import safe_text


def validate_context(context: PlanningContext) -> None:
    if len(context.model_dump_json().encode("utf-8")) > 100_000:
        raise OperationError("planner_context_too_large")
    safe_text(context.model_dump_json())
    plan, campaign = context.plan.body, context.campaign.body
    if (
        not isinstance(plan, c.ContentPlan)
        or not isinstance(campaign, c.Campaign)
        or plan.brand_id != context.brand_id
        or campaign.brand_id != context.brand_id
        or plan.campaign_id != context.campaign.id
        or not 1 <= len(plan.slots) <= 5
        or len({(s.planned_at, s.destination) for s in plan.slots}) != len(plan.slots)
        or len(set(context.fact_ids)) != len(context.fact_ids)
        or any(not campaign.starts_at <= s.planned_at <= campaign.ends_at for s in plan.slots)
    ):
        raise OperationError("planner_context_invalid")
    facts = {r.id for r in context.records if isinstance(r.body, c.ProductFact) and r.confirmed_by}
    if not set(context.fact_ids) <= facts:
        raise OperationError("planner_confirmed_facts_required")
    if any(r.body.brand_id != context.brand_id or not r.confirmed_by for r in context.records):
        raise OperationError("planner_evidence_invalid")
    for row in [context.plan, context.campaign, *context.records]:
        if c.canonical_hash(row.body) != row.content_hash:
            raise OperationError("planner_record_integrity_error")


async def snapshot(
    s: AsyncSession,
    wid: UUID,
    brand: UUID,
    plan_id: UUID,
    content_hash: str,
    fact_ids: list[UUID],
    direction: str,
    knowledge_gaps: list[str],
) -> PlanningContext:
    # As with Editor, callers take knowledge before content. No network under locks.
    await workspace_lock(s, wid)
    plan = await record(s, wid, plan_id, "content_plan", brand)
    body = c.ContentPlan.model_validate(plan.body)
    if plan.content_hash != content_hash:
        raise OperationError("planner_plan_changed")
    if not 1 <= len(body.slots) <= 5:
        raise OperationError("planner_slot_limit_exceeded")
    at = utcnow()
    if any(slot.planned_at <= at for slot in body.slots):
        raise OperationError("planner_slot_elapsed")
    horizon = max(slot.planned_at for slot in body.slots)
    campaign = await record(s, wid, body.campaign_id, "campaign", brand)
    campaign_body = c.Campaign.model_validate(campaign.body)
    await member(s, wid, campaign_body.owner_id)
    # Plans/campaigns are planning intent, not factual evidence. Even a new draft invalidates them.
    for row in (plan, campaign):
        latest = await s.scalar(
            select(func.max(ContentRecord.number)).where(
                ContentRecord.workspace_id == wid,
                ContentRecord.family_id == row.family_id,
            )
        )
        if row.number != latest or row.expires_at <= horizon:
            raise OperationError("planner_structure_stale")
    rows: dict[UUID, c.RecordView] = {}

    async def evidence(rid: UUID, kind: str) -> None:
        if rid in rows:
            if rows[rid].body.kind != kind:
                raise OperationError("planner_evidence_invalid")
            return
        row = await record(s, wid, rid, kind, brand)
        if not await current(s, row, horizon):
            raise OperationError("planner_evidence_stale")
        value = c.RecordView.model_validate(row)
        rows[rid] = value
        if len(rows) > 50:
            raise OperationError("planner_context_too_large")
        item = value.body
        if isinstance(item, (c.ProductFact, c.ProductVersion, c.BrandProfile, c.ClaimPolicy)):
            await evidence(item.source_item_id, "source_item")
        if isinstance(item, c.ProductFact):
            await evidence(item.product_version_id, "product_version")
        if isinstance(item, c.SourceItem) and item.evidence_kind == "hypothesis":
            raise OperationError("planner_hypothesis_is_not_evidence")

    for fid in fact_ids:
        await evidence(fid, "product_fact")
    policies = (
        await s.scalars(
            select(ContentRecord)
            .where(
                ContentRecord.workspace_id == wid,
                ContentRecord.brand_id == brand,
                ContentRecord.kind.in_(["brand_profile", "claim_policy"]),
                ContentRecord.confirmed_by.is_not(None),
            )
            .distinct(ContentRecord.family_id)
            .order_by(ContentRecord.family_id, ContentRecord.number.desc())
            .limit(51)
        )
    ).all()
    if len(policies) > 50:
        raise OperationError("planner_context_too_large")
    for kind in ("brand_profile", "claim_policy"):
        if not any(p.kind == kind for p in policies):
            raise OperationError("planner_policy_required")
    for policy in policies:
        await evidence(policy.id, policy.kind)
    result = PlanningContext(
        brand_id=brand,
        plan=c.RecordView.model_validate(plan),
        campaign=c.RecordView.model_validate(campaign),
        fact_ids=sorted(fact_ids),
        records=[rows[rid] for rid in sorted(rows)],
        direction=direction,
        knowledge_gaps=knowledge_gaps,
    )
    validate_context(result)
    return result


def validate_draft(draft: PlanDraft, context: PlanningContext) -> None:
    validate_context(context)
    safe_text(draft.model_dump_json())
    if (draft.plan_id, draft.content_hash, draft.context_hash) != (
        context.plan.id,
        context.plan.content_hash,
        c.canonical_hash(context),
    ):
        raise OperationError("model_plan_binding_invalid")
    if any(gap not in draft.knowledge_gaps for gap in context.knowledge_gaps):
        raise OperationError("model_plan_gaps_missing")
    if draft.outcome == "insufficient_evidence":
        if draft.slots or not draft.knowledge_gaps:
            raise OperationError("model_plan_abstention_invalid")
        return
    plan, campaign = context.plan.body, context.campaign.body
    assert isinstance(plan, c.ContentPlan) and isinstance(campaign, c.Campaign)
    if sorted(slot.slot_index for slot in draft.slots) != list(range(len(plan.slots))):
        raise OperationError("model_plan_slots_invalid")
    facts = {r.id: r.body for r in context.records if isinstance(r.body, c.ProductFact)}
    for proposed in draft.slots:
        original = plan.slots[proposed.slot_index]
        if (proposed.planned_at, proposed.destination, proposed.owner_id) != (
            original.planned_at,
            original.destination,
            campaign.owner_id,
        ):
            raise OperationError("model_plan_slot_binding_invalid")
        text = proposed.topic + "\n" + proposed.rationale
        for citation in proposed.evidence:
            fact = facts.get(citation.fact_id)
            if (
                citation.fact_id not in context.fact_ids
                or fact is None
                or citation.quote not in text
                or citation.source_quote not in fact.statement
            ):
                raise OperationError("model_plan_evidence_invalid")
