"""Phase-six content contracts. Exact snapshots, never executable source text."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from smm_gpt.domain.operations import DTO, IdempotencyToken

Short = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Text = Annotated[str, Field(min_length=1, max_length=6000, pattern=r"\S")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
IDs = Annotated[list[UUID], Field(max_length=30)]


class RecordBody(DTO):
    name: Short
    brand_id: UUID


class SourceItem(RecordBody):
    kind: Literal["source_item"] = "source_item"
    source_id: UUID
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    excerpt: Text
    observed_at: AwareDatetime
    evidence_kind: Literal["observation", "hypothesis", "owner_input"]


class BrandProfile(RecordBody):
    kind: Literal["brand_profile"] = "brand_profile"
    audience: Short
    tone: Text
    source_item_id: UUID


class ProductVersion(RecordBody):
    kind: Literal["product_version"] = "product_version"
    product_id: UUID
    description: Text
    source_item_id: UUID


class ProductFact(RecordBody):
    kind: Literal["product_fact"] = "product_fact"
    product_version_id: UUID
    source_item_id: UUID
    statement: Text


class ClaimRule(DTO):
    phrase: Short
    severity: Literal["blocker", "warning"]
    alternative: Annotated[str, Field(max_length=300)] = ""


class ClaimPolicy(RecordBody):
    kind: Literal["claim_policy"] = "claim_policy"
    source_item_id: UUID
    jurisdiction: Short
    rules: Annotated[list[ClaimRule], Field(max_length=30)] = Field(default_factory=list)
    required_disclaimers: Annotated[list[Short], Field(max_length=10)] = Field(default_factory=list)


class Research(RecordBody):
    kind: Literal["research"] = "research"
    source_item_ids: IDs
    observations: Text
    hypotheses: Annotated[str, Field(max_length=3000)] = ""


class Campaign(RecordBody):
    kind: Literal["campaign"] = "campaign"
    goal: Text
    kpi: Short
    owner_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("invalid_period")
        return self


class Slot(DTO):
    planned_at: AwareDatetime
    topic: Short
    destination: Annotated[str, Field(pattern=r"^vk:group:[1-9][0-9]{0,19}$")]


class ContentPlan(RecordBody):
    kind: Literal["content_plan"] = "content_plan"
    campaign_id: UUID
    slots: Annotated[list[Slot], Field(min_length=1, max_length=30)]


class Brief(RecordBody):
    kind: Literal["brief"] = "brief"
    goal: Text
    audience: Short
    product_id: UUID | None = None
    campaign_id: UUID | None = None
    research_id: UUID | None = None


class Idea(RecordBody):
    kind: Literal["idea"] = "idea"
    brief_id: UUID
    rationale: Text


type Artifact = Annotated[
    SourceItem
    | BrandProfile
    | ProductVersion
    | ProductFact
    | ClaimPolicy
    | Research
    | Campaign
    | ContentPlan
    | Brief
    | Idea,
    Field(discriminator="kind"),
]
ARTIFACT: TypeAdapter[Artifact] = TypeAdapter(Artifact)
type RecordKind = Literal[
    "source_item",
    "brand_profile",
    "product_version",
    "product_fact",
    "claim_policy",
    "research",
    "campaign",
    "content_plan",
    "brief",
    "idea",
]


def canonical_hash(value: DTO | dict[str, object]) -> str:
    body = value.model_dump(mode="json") if isinstance(value, DTO) else value
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class RecordView(DTO):
    id: UUID
    family_id: UUID
    number: int
    created_at: datetime
    expires_at: datetime
    confirmed_by: UUID | None
    content_hash: str
    body: Artifact


class Attachment(DTO):
    file_id: UUID
    alt: Short
    rights_confirmed: Literal[True]


class Variant(DTO):
    platform: Literal["vk"] = "vk"
    destination: Annotated[str, Field(pattern=r"^vk:group:[1-9][0-9]{0,19}$")]
    text: Annotated[str, Field(min_length=1, max_length=10000, pattern=r"\S")]
    media: Annotated[list[Attachment], Field(max_length=10)] = Field(default_factory=list)


class RevisionBody(DTO):
    variants: Annotated[list[Variant], Field(min_length=1, max_length=3)]
    fact_ids: IDs = Field(default_factory=list)
    knowledge_gaps: Annotated[list[Short], Field(max_length=20)] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_targets(self) -> Self:
        if len({v.destination for v in self.variants}) != len(self.variants):
            raise ValueError("duplicate_destination")
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("duplicate_fact")
        return self


class RevisionView(DTO):
    id: UUID
    number: int
    created_at: datetime
    actor_id: UUID
    content_hash: str
    body: RevisionBody
    media_manifest: list[dict[str, object]]


class Finding(DTO):
    code: str
    severity: Literal["blocker", "warning"]
    location: str
    record_id: UUID | None = None


class Preflight(DTO):
    revision_id: UUID
    content_hash: str
    checked_at: datetime
    findings: list[Finding]
    checked_record_ids: list[UUID]
    passed: bool
    ai_review: Literal["not_run"] = "not_run"


type PostState = Literal["draft", "in_review", "rejected", "approved", "package_ready"]


class PostSummary(DTO):
    id: UUID
    brand_id: UUID
    title: str
    state: PostState
    version: int
    current_revision_id: UUID | None


class DecisionView(DTO):
    id: UUID
    revision_id: UUID
    actor_id: UUID
    created_at: datetime
    decision: Literal["approve", "reject"]
    reason: str
    content_hash: str


class CommentView(DTO):
    id: UUID
    revision_id: UUID
    actor_id: UUID
    created_at: datetime
    text: str


class PostView(PostSummary):
    brief_id: UUID
    idea_id: UUID | None
    revisions: list[RevisionView]
    decisions: list[DecisionView]
    comments: list[CommentView]
    history_truncated: bool
    active_approval_id: UUID | None


class WorkingCopyView(DTO):
    post_id: UUID
    version: int
    base_version: int
    expires_at: datetime
    body: RevisionBody


class PackageSummary(DTO):
    id: UUID
    post_id: UUID
    revision_id: UUID
    created_at: datetime
    content_hash: str
    scheduled_at: datetime
    timezone: str
    mode: Literal["manual"] = "manual"
    status: Literal["active", "cancelled", "stale", "expired"]


class PackageView(PackageSummary):
    manifest: dict[str, object]


class TaskContext(DTO):
    item_id: UUID
    version: int
    assignee_id: UUID | None
    due_at: AwareDatetime | None
    campaign_id: UUID | None
    dependencies: list[UUID]


type HistoryKind = Literal["revisions", "decisions", "comments", "reviews"]


class HistoryEntry(DTO):
    id: UUID
    created_at: AwareDatetime
    kind: HistoryKind
    data: dict[str, object]


class Command(DTO):
    idempotency_key: IdempotencyToken


class CreateCatalog(Command):
    action: Literal["catalog_create"] = "catalog_create"
    kind: Literal["brands", "products", "sources"]
    name: Short


class CreateRecord(Command):
    action: Literal["record_create"] = "record_create"
    body: Artifact
    expires_at: AwareDatetime
    replaces_id: UUID | None = None


class ConfirmRecord(Command):
    action: Literal["record_confirm"] = "record_confirm"
    record_id: UUID
    content_hash: Hash
    confirmed: Literal[True]


class CreatePost(Command):
    action: Literal["post_create"] = "post_create"
    brief_id: UUID
    idea_id: UUID | None = None
    title: Short


class SaveRevision(Command):
    action: Literal["revision_save"] = "revision_save"
    post_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    body: RevisionBody


class SaveWorkingCopy(Command):
    action: Literal["working_copy_save"] = "working_copy_save"
    post_id: UUID
    expected_copy_version: Annotated[int, Field(ge=0)]
    base_version: Annotated[int, Field(ge=1)]
    body: RevisionBody


class RequestReview(Command):
    action: Literal["review_request"] = "review_request"
    post_id: UUID
    expected_version: Annotated[int, Field(ge=1)]


class DecidePost(Command):
    action: Literal["post_decide"] = "post_decide"
    post_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    revision_id: UUID
    content_hash: Hash
    decision: Literal["approve", "reject"]
    reason: Short
    human_confirmed: Literal[True]
    claims_reviewed: Literal[True]


class AddComment(Command):
    action: Literal["comment_add"] = "comment_add"
    post_id: UUID
    revision_id: UUID
    text: Annotated[str, Field(min_length=1, max_length=2000)]


class PreparePackage(Command):
    action: Literal["package_prepare"] = "package_prepare"
    post_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    revision_id: UUID
    content_hash: Hash
    scheduled_at: AwareDatetime
    human_confirmed: Literal[True]


class CancelPackage(Command):
    action: Literal["package_cancel"] = "package_cancel"
    package_id: UUID
    expected_version: Annotated[int, Field(ge=1)]


class AssignTask(Command):
    action: Literal["task_assign"] = "task_assign"
    item_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    assignee_id: UUID
    due_at: AwareDatetime
    campaign_id: UUID | None = None


class DependTask(Command):
    action: Literal["task_depend"] = "task_depend"
    item_id: UUID
    depends_on: UUID
    expected_version: Annotated[int, Field(ge=1)]
    remove: bool = False


type ContentCommand = Annotated[
    CreateCatalog
    | CreateRecord
    | ConfirmRecord
    | CreatePost
    | SaveRevision
    | SaveWorkingCopy
    | RequestReview
    | DecidePost
    | AddComment
    | PreparePackage
    | CancelPackage
    | AssignTask
    | DependTask,
    Field(discriminator="action"),
]


class CommandResult(DTO):
    entity_id: UUID
    version: int
    action: str


def check_version(actual: int, expected: int) -> None:
    from smm_gpt.domain.operations import OperationError

    if actual != expected:
        raise OperationError("version_conflict")
