"""Transport-independent commands and bounded, public read models."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from smm_gpt.domain.access import Permission


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, hide_input_in_errors=True)


class OperationError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


class ErrorInfo(DTO):
    code: str
    correlation_id: UUID


class ErrorResponse(DTO):
    error: ErrorInfo
    detail: str


class WorkspaceView(DTO):
    id: UUID
    name: str
    timezone: str
    permissions: list[Permission]


class SessionView(DTO):
    user_id: UUID
    display_name: str
    mfa: bool
    access_version: str
    workspaces: list[WorkspaceView]


class WorkState(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


TRANSITIONS = {
    WorkState.OPEN: frozenset({WorkState.IN_PROGRESS, WorkState.CANCELLED}),
    WorkState.IN_PROGRESS: frozenset({WorkState.DONE, WorkState.CANCELLED}),
    WorkState.DONE: frozenset(),
    WorkState.CANCELLED: frozenset(),
}

IdempotencyToken = Annotated[str, Field(min_length=8, max_length=128)]
PageSize = Annotated[int, Field(ge=1, le=50)]


class CreateWorkItem(DTO):
    title: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    brief: Annotated[str, Field(max_length=2000)] = ""
    idempotency_key: IdempotencyToken


class TransitionWorkItem(DTO):
    expected_version: Annotated[int, Field(ge=1)]
    state: WorkState


class WorkItemView(DTO):
    id: UUID
    workspace_id: UUID
    created_at: datetime
    title: str
    brief: str
    state: WorkState
    version: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_transitions(self) -> list[WorkState]:
        return sorted(TRANSITIONS[self.state])


class CatalogKind(StrEnum):
    BRANDS = "brands"
    PRODUCTS = "products"
    SOURCES = "sources"


class CatalogView(DTO):
    id: UUID
    name: str


class AuditView(DTO):
    id: UUID
    created_at: datetime
    action: str
    outcome: str
    target_id: UUID | None
    request_id: UUID


class Page[T](DTO):
    items: list[T]
    next_cursor: UUID | None = None
