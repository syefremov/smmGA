"""Testing-only text proposals; no revision writes, media or approval fields."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from smm_gpt.domain.content import Hash, Short
from smm_gpt.domain.editor import EditorContext
from smm_gpt.domain.knowledge import ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken

Direction = Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
Quote = Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]


class RunCopyDraft(DTO):
    idempotency_key: IdempotencyToken
    profile: Literal["copywriter"] = "copywriter"
    brand_id: UUID
    post_id: UUID
    revision_id: UUID
    content_hash: Hash
    direction: Direction
    profile_version_id: UUID
    profile_selection_id: UUID
    testing_only: Literal[True]


class CopywritingContext(DTO):
    contract: Literal["copywriting-context-v1"] = "copywriting-context-v1"
    # Reuse the proven SQL snapshot without changing the editor's existing payload contract.
    source: EditorContext
    direction: Direction


class CopyEvidence(DTO):
    fact_id: UUID
    quote: Quote
    source_quote: Quote


class CopyVariant(DTO):
    variant_index: Annotated[int, Field(ge=0, le=2)]
    text: Annotated[str, Field(min_length=1, max_length=3000, pattern=r"\S")]
    evidence: Annotated[list[CopyEvidence], Field(min_length=1, max_length=10)]


class CopyDraft(DTO):
    revision_id: UUID
    content_hash: Hash
    context_hash: Hash
    outcome: Literal["draft", "insufficient_evidence"]
    variants: Annotated[list[CopyVariant], Field(max_length=3)]
    warnings: Annotated[list[ShortText], Field(max_length=10)]
    knowledge_gaps: Annotated[list[Short], Field(max_length=30)]
