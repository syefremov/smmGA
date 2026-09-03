"""Text-only editorial candidates, never content mutations or human approvals."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from smm_gpt.domain.content import Finding, Hash, RecordView, RevisionView
from smm_gpt.domain.knowledge import ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken


class RunEditorialReview(DTO):
    idempotency_key: IdempotencyToken
    profile: Literal["editor"] = "editor"
    brand_id: UUID
    post_id: UUID
    revision_id: UUID
    content_hash: Hash
    profile_version_id: UUID
    profile_selection_id: UUID
    testing_only: Literal[True]


class EditorContext(DTO):
    contract: Literal["editor-context-v1"] = "editor-context-v1"
    post_id: UUID
    brand_id: UUID
    revision: RevisionView
    brief: RecordView
    records: Annotated[list[RecordView], Field(max_length=100)]
    preflight_findings: Annotated[list[Finding], Field(max_length=250)]


class EditorialFinding(DTO):
    category: Literal["facts", "claims", "tone", "format", "accessibility", "privacy"]
    severity: Literal["info", "warning", "blocking"]
    location: Literal["revision", "variant", "brief", "evidence"]
    variant_index: Annotated[int, Field(ge=0, le=2)] | None
    quote: Annotated[str, Field(max_length=500)]
    description: ShortText
    suggestion: ShortText
    record_ids: Annotated[list[UUID], Field(max_length=10)]


class EditorialReview(DTO):
    revision_id: UUID
    content_hash: Hash
    context_hash: Hash
    recommendation: Literal["pass", "needs_changes", "needs_human_decision"]
    summary: ShortText
    findings: Annotated[list[EditorialFinding], Field(max_length=20)]
