"""Owner-curated retrieval benchmarks, not model truth or production activation."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from smm_gpt.domain.knowledge import ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Category = Literal["exact", "paraphrase", "no_answer", "freshness", "conflict", "injection"]
Audience = Literal["workspace", "owner"]
Query = Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
Identifiers = Annotated[list[UUID], Field(max_length=10)]


class EvalCase(DTO):
    key: Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,40}$")]
    category: Category
    audience: Audience
    query: Query
    expected_document_ids: Identifiers
    forbidden_document_ids: Identifiers = Field(default_factory=list)

    @model_validator(mode="after")
    def expectations(self) -> "EvalCase":
        expected, forbidden = set(self.expected_document_ids), set(self.forbidden_document_ids)
        if len(expected) != len(self.expected_document_ids) or len(forbidden) != len(
            self.forbidden_document_ids
        ):
            raise ValueError("duplicate_source")
        if expected & forbidden:
            raise ValueError("contradictory_expectations")
        if self.category == "no_answer" and expected:
            raise ValueError("no_answer_must_be_empty")
        if self.category in ("exact", "paraphrase", "conflict") and not expected:
            raise ValueError("expected_sources_required")
        if self.category == "conflict" and len(expected) < 2:
            raise ValueError("conflict_requires_two_sources")
        return self


class EvalThresholds(DTO):
    # Per-case thresholds, not averages which can conceal a serious failed case.
    precision: Annotated[float, Field(ge=0.8, le=1)] = 0.8
    recall: Annotated[float, Field(ge=0.8, le=1)] = 1.0
    max_case_ms: Annotated[int, Field(ge=1, le=2000)] = 1000


class EvalDefinition(DTO):
    title: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    origin: Literal["synthetic", "owner_curated"]
    limit: Annotated[int, Field(ge=1, le=10)] = 5
    thresholds: EvalThresholds = Field(default_factory=EvalThresholds)
    cases: Annotated[list[EvalCase], Field(min_length=1, max_length=25)]

    @model_validator(mode="after")
    def unique_cases(self) -> "EvalDefinition":
        if len({c.key for c in self.cases}) != len(self.cases) or len(
            {(c.audience, c.query.strip().casefold()) for c in self.cases}
        ) != len(self.cases):
            raise ValueError("duplicate_case")
        if any(len(c.expected_document_ids) > self.limit for c in self.cases):
            raise ValueError("expectations_exceed_top_k")
        return self


class SubmitEval(DTO):
    action: Literal["dataset_submit"] = "dataset_submit"
    idempotency_key: IdempotencyToken
    brand_id: UUID
    # A revision appends to the exact latest immutable dataset, never overwrites it.
    previous_dataset_id: UUID | None = None
    definition: EvalDefinition


class RunEval(DTO):
    action: Literal["evaluation_run"] = "evaluation_run"
    idempotency_key: IdempotencyToken
    dataset_id: UUID
    dataset_hash: Hash


class ReviewEval(DTO):
    action: Literal["evaluation_review"] = "evaluation_review"
    idempotency_key: IdempotencyToken
    run_id: UUID
    report_hash: Hash
    decision: Literal["accept_baseline", "reject"]
    reason: ShortText
    human_confirmed: Literal[True]


EvalCommand = Annotated[SubmitEval | RunEval | ReviewEval, Field(discriminator="action")]


class EvalResult(DTO):
    entity_id: UUID
    content_hash: Hash


class DatasetView(DTO):
    id: UUID
    actor_id: UUID
    brand_id: UUID
    family_id: UUID
    number: int
    content_hash: str
    created_at: datetime
    definition: EvalDefinition


class CorpusSource(DTO):
    document_id: UUID
    document_version_id: UUID
    index_id: UUID
    content_hash: str
    parser_version: str
    chunking_version: str
    visibility: Audience
    effective_from: datetime
    effective_to: datetime


class EvalHit(DTO):
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_id: UUID
    content_hash: str


class CaseScore(DTO):
    key: str
    precision: float
    recall: float
    citation_validity: float
    negative_pass: bool
    forbidden_pass: bool
    latency_ms: float
    passed: bool
    missing_document_ids: list[UUID]
    unexpected_document_ids: list[UUID]
    hits: list[EvalHit]


class EvalReport(DTO):
    algorithm: Literal["ru-simple-v1"] = "ru-simple-v1"
    metric_version: Literal["source-macro-v1"] = "source-macro-v1"
    passed: bool
    precision: float
    recall: float
    citation_validity: float
    negative_pass: bool
    forbidden_pass: bool
    duration_ms: float
    cases: list[CaseScore]


class EvalRunView(DTO):
    id: UUID
    actor_id: UUID
    brand_id: UUID
    dataset_id: UUID
    dataset_hash: str
    corpus_hash: str
    report_hash: str
    created_at: datetime
    report: EvalReport
    decision: Literal["accept_baseline", "reject"] | None = None
    stale: bool
    stale_reasons: list[str]
    acceptance_blockers: list[str]
    baseline_current: bool
    warning: str = (
        "FTS benchmark only: not semantic truth, employee RLS proof or production approval."
    )


class EvalRunDetail(EvalRunView):
    definition: EvalDefinition
    corpus: list[CorpusSource]
    review_reason: str | None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None


def acceptance_blockers(definition: EvalDefinition) -> list[str]:
    reasons = []
    if definition.origin != "owner_curated":
        reasons.append("synthetic_dataset")
    if len(definition.cases) < 8:
        reasons.append("at_least_eight_cases_required")
    required = {"exact", "paraphrase", "no_answer", "freshness", "conflict", "injection"}
    if not required <= {c.category for c in definition.cases}:
        reasons.append("category_coverage_incomplete")
    if {c.audience for c in definition.cases} != {"workspace", "owner"}:
        reasons.append("audience_coverage_incomplete")
    if not any(c.forbidden_document_ids for c in definition.cases):
        reasons.append("forbidden_source_case_required")
    return reasons
