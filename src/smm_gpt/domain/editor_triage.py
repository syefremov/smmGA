"""Personal human triage of AI findings; never content approval or proof of a fix."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from smm_gpt.domain.content import Hash
from smm_gpt.domain.knowledge import ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken

FindingIndex = Annotated[int, Field(ge=0, le=19, strict=True)]
TriageStatus = Literal["open", "needs_changes", "dismissed"]
HistoryCursor = Annotated[int, Field(ge=1)]


class DecideEditorialFinding(DTO):
    idempotency_key: IdempotencyToken
    artifact_id: UUID
    artifact_hash: Hash
    revision_id: UUID
    content_hash: Hash
    finding_index: FindingIndex
    finding_hash: Hash
    expected_version: Annotated[int, Field(ge=0, strict=True)]
    status: TriageStatus
    reason: ShortText
    human_confirmed: Literal[True]


class EditorialDecisionView(DTO):
    id: UUID
    run_id: UUID
    artifact_id: UUID
    artifact_hash: Hash
    revision_id: UUID
    content_hash: Hash
    finding_index: FindingIndex
    finding_hash: Hash
    sequence: int
    status: TriageStatus
    reason: str
    actor_id: UUID
    created_at: datetime


class EditorialDecisionReceipt(DTO):
    decision: EditorialDecisionView
    historical_only: Literal[True] = True
    warning: str = "Историческая запись, не текущий статус и не одобрение поста. Перечитайте отчёт."


class EditorialFindingState(DTO):
    finding_index: FindingIndex
    finding_hash: Hash
    status: TriageStatus = "open"
    latest_decision: EditorialDecisionView | None = None


class EditorialTriageView(DTO):
    run_id: UUID
    artifact_id: UUID
    artifact_hash: Hash
    revision_id: UUID
    content_hash: Hash
    version: int
    findings: list[EditorialFindingState]
    recent_history: list[EditorialDecisionView]
    next_before: int | None
    warning: str = "Решения относятся только к замечаниям AI. Они не исправляют и не одобряют пост."


class EditorialHistory(DTO):
    items: list[EditorialDecisionView]
    next_before: int | None
    historical_only: Literal[True] = True
