"""Human transfer of exact topics and disclosed notes; never approval or scheduling."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from smm_gpt.domain.content import ContentPlan, Hash, Short
from smm_gpt.domain.knowledge import ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken
from smm_gpt.domain.planner import PlanSlot


class PlanNotesBody(DTO):
    fact_ids: Annotated[list[UUID], Field(min_length=1, max_length=10)]
    evidence_record_ids: Annotated[list[UUID], Field(min_length=1, max_length=50)]
    slots: Annotated[list[PlanSlot], Field(min_length=1, max_length=5)]
    warnings: Annotated[list[ShortText], Field(max_length=10)]
    knowledge_gaps: Annotated[list[Short], Field(max_length=20)]


class PlanAdoptionPreview(DTO):
    run_id: UUID
    artifact_id: UUID
    artifact_hash: Hash
    input_id: UUID
    input_hash: Hash
    source_plan_id: UUID
    source_content_hash: Hash
    source_plan_number: int
    expires_at: datetime
    proposed_content_hash: Hash
    notes_hash: Hash
    preview_hash: Hash
    body: ContentPlan
    notes: PlanNotesBody
    warning: str = (
        "Предпросмотр не является согласием. После отдельного подтверждения новая версия плана, "
        "темы, ответственный, обоснования, цитаты, fact/evidence IDs, warnings и пробелы будут "
        "доступны читателям контента workspace. Личный запрос и причины решения не раскрываются. "
        "Это черновой план, не одобрение или расписание публикации."
    )


class AdoptPlanDraft(DTO):
    idempotency_key: IdempotencyToken
    artifact_id: UUID
    artifact_hash: Hash
    preview_hash: Hash
    proposed_content_hash: Hash
    notes_hash: Hash
    expected_plan_number: Annotated[int, Field(ge=1, strict=True)]
    reason: ShortText
    human_confirmed: Literal[True]
    share_with_workspace_confirmed: Literal[True]


class PlanAdoptionView(DTO):
    id: UUID
    run_id: UUID
    artifact_id: UUID
    artifact_hash: Hash
    input_id: UUID
    input_hash: Hash
    source_plan_id: UUID
    source_content_hash: Hash
    plan_id: UUID
    content_hash: Hash
    plan_number: int
    notes_id: UUID
    notes_hash: Hash
    preview_hash: Hash
    actor_id: UUID
    created_at: datetime
    reason: str
    historical_only: Literal[True] = True
    warning: str = (
        "История принятия, не актуальность или одобрение. Перечитайте план и ограничения."
    )


class PlanNotesView(DTO):
    id: UUID
    plan_id: UUID
    plan_hash: Hash
    content_hash: Hash
    actor_id: UUID
    created_at: datetime
    body: PlanNotesBody
    requested_plan_id: UUID
    exact_version: bool
    historical_only: Literal[True] = True
    warning: str = (
        "Сохранённые основания и пробелы принятого AI-плана. Это история, не проверка текущих "
        "фактов или одобрение. Для последующей версии показываются ограничения предка; "
        "они не подтверждают новый текст и не считаются автоматически устранёнными."
    )
