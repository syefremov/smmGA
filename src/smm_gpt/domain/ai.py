"""AI definitions are not human roles. No executable tools or approval operations exist here."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from smm_gpt.domain.knowledge import Citation, ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken

ProfileName = Literal[
    "product_expert",
    "research_scout",
    "analyst",
    "content_planner",
    "copywriter",
    "visual_creator",
    "editor",
    "publisher",
]


class Profile(DTO):
    name: ProfileName
    version: Literal["reference-assessment-v1"] = "reference-assessment-v1"
    purpose: str
    status: Literal["testing", "blocked"]
    accepted_inputs: list[str]
    output_schema: str = "ReferenceAssessment"
    allowed_capabilities: list[str] = Field(
        default_factory=lambda: ["knowledge.search", "assessment.propose"]
    )
    denied_capabilities: list[str] = Field(
        default_factory=lambda: [
            "content.write",
            "content.approve",
            "publish",
            "tools.call",
            "network.fetch",
            "memory.activate",
            "profile.activate",
        ]
    )
    quality_gates: list[str] = Field(
        default_factory=lambda: ["schema", "current_citations", "human_review", "corpus_evals"]
    )
    escalation: str = "Недостающие сведения — пробел знаний; решение принимает владелец."
    blocked_reason: str | None = None


PROFILES = (
    Profile(
        name="product_expert",
        purpose="Предложить вопросы и сведения для проверки продукта.",
        status="testing",
        accepted_inputs=["question", "owner_reviewed_references"],
    ),
    Profile(
        name="research_scout",
        purpose="Разобрать разрешённые источники без поиска в интернете.",
        status="testing",
        accepted_inputs=["question", "owner_reviewed_references"],
    ),
    Profile(
        name="analyst",
        purpose="Анализировать измеренные показатели.",
        status="blocked",
        accepted_inputs=["metric_snapshots"],
        blocked_reason="metric_snapshots_required",
    ),
    Profile(
        name="content_planner",
        purpose="Предложить план из проверенных входов.",
        status="blocked",
        accepted_inputs=["campaign", "facts"],
        blocked_reason="typed_planner_evals_required",
    ),
    Profile(
        name="copywriter",
        purpose="Предложить новую редакцию по brief.",
        status="blocked",
        accepted_inputs=["brief", "facts", "claim_policy"],
        blocked_reason="typed_copywriter_evals_required",
    ),
    Profile(
        name="visual_creator",
        purpose="Подготовить варианты, проверить происхождение и права.",
        status="blocked",
        accepted_inputs=["revision", "licensed_assets"],
        blocked_reason="media_rights_pipeline_required",
    ),
    Profile(
        name="editor",
        purpose="Проверить точную редакцию без human approval.",
        status="blocked",
        accepted_inputs=["revision", "claim_policy"],
        blocked_reason="typed_reviewer_evals_required",
    ),
    Profile(
        name="publisher",
        purpose="Проверить пакет, не редактируя редакцию.",
        status="blocked",
        accepted_inputs=["approved_package"],
        blocked_reason="use_manual_package_workflow",
    ),
)


class SourcedStatement(DTO):
    text: ShortText
    citation_ids: Annotated[list[UUID], Field(min_length=1, max_length=10)]
    evidence: Literal["source_observation", "conflicting"]


class ReferenceAssessment(DTO):
    statements: Annotated[list[SourcedStatement], Field(max_length=10)]
    hypotheses: Annotated[list[ShortText], Field(max_length=10)]
    knowledge_gaps: Annotated[list[ShortText], Field(max_length=10)]
    # No tool calls, hidden reasoning, permanent facts, approval flags or arbitrary artifacts.


class RunAssessment(DTO):
    idempotency_key: IdempotencyToken
    profile: ProfileName
    brand_id: UUID
    question: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    testing_only: Literal[True]


class AIRunView(DTO):
    id: UUID
    profile: ProfileName
    profile_version: str
    state: str
    error_code: str | None
    provider: str
    model: str
    retrieval_run_id: UUID | None
    assessment: ReferenceAssessment | None = None
    citations: list[Citation] = Field(default_factory=list)
    usage: dict[str, int | str | None]
    warning: str = "Тестовый кандидат, не подтверждённый факт и не одобрение публикации."
