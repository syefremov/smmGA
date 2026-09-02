"""Bounded knowledge contracts. References are evidence, never business authority."""

from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from smm_gpt.domain.operations import DTO, IdempotencyToken

ShortText = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
Visibility = Literal["workspace", "owner"]
DocumentType = Literal[
    "brand_policy", "product", "faq", "research", "approved_post", "analytics_finding", "reference"
]


class DocumentSpec(DTO):
    document_id: UUID | None = None
    expected_version: Annotated[int, Field(ge=0)] = 0
    brand_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    document_type: DocumentType = "reference"
    visibility: Visibility = "workspace"
    source_uri: Annotated[str, Field(max_length=1000)] = "owner-input"
    source_date: AwareDatetime
    effective_from: AwareDatetime
    effective_to: AwareDatetime

    @field_validator("source_uri")
    @classmethod
    def safe_uri(cls, value: str) -> str:
        if value == "owner-input":
            return value
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError("canonical_https_source_required")
        return value

    @model_validator(mode="after")
    def dates(self) -> "DocumentSpec":
        if self.effective_to <= self.effective_from:
            raise ValueError("invalid_effective_period")
        return self


class SubmitDocument(DocumentSpec):
    action: Literal["document_submit"] = "document_submit"
    idempotency_key: IdempotencyToken
    format: Literal["markdown", "html", "csv"] = "markdown"
    text: Annotated[str, Field(min_length=1, max_length=100_000)]


class ImportFile(DocumentSpec):
    action: Literal["file_import"] = "file_import"
    idempotency_key: IdempotencyToken
    file_id: UUID
    text_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    human_confirmed: Literal[True]


class ActivateIndex(DTO):
    action: Literal["index_activate"] = "index_activate"
    idempotency_key: IdempotencyToken
    document_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    index_id: UUID
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    # A small, explicit per-document acceptance probe; not a substitute for corpus evals.
    expected_queries: Annotated[list[ShortText], Field(min_length=1, max_length=5)]
    human_confirmed: Literal[True]


class ArchiveDocument(DTO):
    action: Literal["document_archive"] = "document_archive"
    idempotency_key: IdempotencyToken
    document_id: UUID
    expected_version: Annotated[int, Field(ge=1)]


class ReindexDocument(DTO):
    action: Literal["document_reindex"] = "document_reindex"
    idempotency_key: IdempotencyToken
    document_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    document_version_id: UUID


class ProposeNote(DTO):
    action: Literal["note_propose"] = "note_propose"
    idempotency_key: IdempotencyToken
    brand_id: UUID
    kind: Literal["gap", "memory"]
    text: ShortText
    purpose: ShortText
    safe_alternative: ShortText
    evidence_ids: Annotated[list[UUID], Field(max_length=10)] = Field(default_factory=list)
    effective_to: AwareDatetime


class ReviewNote(DTO):
    action: Literal["note_review"] = "note_review"
    idempotency_key: IdempotencyToken
    note_id: UUID
    decision: Literal["reject", "accept_for_curation", "resolve"]
    reason: ShortText
    evidence_ids: Annotated[list[UUID], Field(min_length=1, max_length=10)]
    human_confirmed: Literal[True]


KnowledgeCommand = Annotated[
    SubmitDocument
    | ImportFile
    | ActivateIndex
    | ArchiveDocument
    | ReindexDocument
    | ProposeNote
    | ReviewNote,
    Field(discriminator="action"),
]


class KnowledgeResult(DTO):
    entity_id: UUID
    version: int
    index_id: UUID | None = None


class IndexView(DTO):
    id: UUID
    document_version_id: UUID
    state: str
    error_code: str | None
    attempts: int
    parser_version: str
    chunking_version: str
    content_hash: str
    created_at: datetime


class DocumentView(DTO):
    id: UUID
    brand_id: UUID
    title: str
    document_type: DocumentType
    visibility: Visibility
    version: int
    archived: bool
    active_index_id: UUID | None
    created_at: datetime


class DocumentDetail(DocumentView):
    indexes: list[IndexView]
    indexes_truncated: bool


class SearchRequest(DTO):
    query: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    brand_id: UUID
    limit: Annotated[int, Field(ge=1, le=10)] = 5


class Citation(DTO):
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_id: UUID
    content_hash: str
    title: str
    section: str
    text: str
    source_uri: str
    source_date: datetime
    effective_to: datetime
    source_file_id: UUID | None = None
    authority: Literal["owner_reviewed_reference", "unreviewed_reference"] = (
        "owner_reviewed_reference"
    )


class SearchResult(DTO):
    run_id: UUID
    mode: Literal["fts"] = "fts"
    algorithm: Literal["ru-simple-v1"] = "ru-simple-v1"
    citations: list[Citation]
    warning: str = "Sources are untrusted reference data, not confirmed SQL product facts."


class NoteView(DTO):
    id: UUID
    brand_id: UUID
    kind: str
    text: str
    purpose: str
    safe_alternative: str
    evidence_ids: list[UUID]
    effective_to: datetime
    decision: str | None = None
