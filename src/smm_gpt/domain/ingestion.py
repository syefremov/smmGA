"""Controls for local ingestion only; never dispatch or cancel paid AI runs."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from smm_gpt.domain.operations import DTO, IdempotencyToken

JobKind = Literal["index", "file"]


class CancelIngestion(DTO):
    idempotency_key: IdempotencyToken
    kind: JobKind
    job_id: UUID
    expected_version: Annotated[int, Field(ge=1)]


class IngestionReceipt(DTO):
    kind: JobKind
    job_id: UUID
    state: Literal["cancelled"] = "cancelled"
    version: int


class IngestionJob(DTO):
    id: UUID
    kind: JobKind
    actor_id: UUID
    state: str
    version: int
    attempts: int
    error_code: str | None
    document_id: UUID | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class IngestionEvent(DTO):
    version: int
    state: str
    attempts: int
    error_code: str | None
    actor_id: UUID | None
    created_at: datetime


class IngestionHistory(DTO):
    kind: JobKind
    job_id: UUID
    events: list[IngestionEvent]
    truncated: bool
