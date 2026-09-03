"""A bounded proposal for human-specified slots, never an executable schedule."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from smm_gpt.domain.content import Hash, RecordView, Short
from smm_gpt.domain.copywriter import Direction, Quote
from smm_gpt.domain.knowledge import ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken


class RunPlanDraft(DTO):
    idempotency_key: IdempotencyToken
    profile: Literal["content_planner"] = "content_planner"
    brand_id: UUID
    plan_id: UUID
    content_hash: Hash
    fact_ids: Annotated[list[UUID], Field(min_length=1, max_length=10)]
    direction: Direction
    knowledge_gaps: Annotated[list[Short], Field(max_length=10)]
    profile_version_id: UUID
    profile_selection_id: UUID
    testing_only: Literal[True]

    @model_validator(mode="after")
    def distinct_facts(self) -> Self:
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("duplicate_fact")
        return self


class PlanningContext(DTO):
    contract: Literal["planning-context-v1"] = "planning-context-v1"
    brand_id: UUID
    plan: RecordView
    campaign: RecordView
    fact_ids: Annotated[list[UUID], Field(min_length=1, max_length=10)]
    records: Annotated[list[RecordView], Field(max_length=50)]
    direction: Direction
    knowledge_gaps: Annotated[list[Short], Field(max_length=10)]


class PlanEvidence(DTO):
    fact_id: UUID
    quote: Quote
    source_quote: Quote


class PlanSlot(DTO):
    slot_index: Annotated[int, Field(ge=0, le=4)]
    planned_at: AwareDatetime
    destination: Annotated[str, Field(pattern=r"^vk:group:[1-9][0-9]{0,19}$")]
    owner_id: UUID
    topic: Short
    rationale: Direction
    evidence: Annotated[list[PlanEvidence], Field(min_length=1, max_length=3)]


class PlanDraft(DTO):
    plan_id: UUID
    content_hash: Hash
    context_hash: Hash
    outcome: Literal["draft", "insufficient_evidence"]
    slots: Annotated[list[PlanSlot], Field(max_length=5)]
    warnings: Annotated[list[ShortText], Field(max_length=10)]
    knowledge_gaps: Annotated[list[Short], Field(max_length=20)]
