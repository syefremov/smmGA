"""Actor-private run reservations and append-only AI outputs."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from smm_gpt.infrastructure.content_models import Tenant, tenant_args
from smm_gpt.infrastructure.database import Base


class AIRun(Tenant, Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        *tenant_args(brand_id="brands", retrieval_run_id="retrieval_runs"),
        UniqueConstraint("workspace_id", "actor_id", "key_hash"),
        ForeignKeyConstraint(
            ["workspace_id", "profile", "profile_version_id", "profile_selection_id"],
            [
                "ai_profile_decisions.workspace_id",
                "ai_profile_decisions.profile",
                "ai_profile_decisions.version_id",
                "ai_profile_decisions.id",
            ],
            name="fk_ai_run_profile_selection",
        ),
        CheckConstraint(
            "(profile_version_id IS NULL) = (profile_selection_id IS NULL)",
            name="ai_run_profile_pair",
        ),
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    brand_id: Mapped[UUID]
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    profile: Mapped[str] = mapped_column(String(32))
    profile_version: Mapped[str] = mapped_column(String(80))
    profile_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    profile_version_id: Mapped[UUID | None]
    profile_selection_id: Mapped[UUID | None]
    state: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    retrieval_run_id: Mapped[UUID | None]
    usage: Mapped[dict[str, int | str | None]] = mapped_column(JSON)
    identity_id: Mapped[UUID | None] = mapped_column(ForeignKey("user_identities.id"))
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    lease_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIInput(Tenant, Base):
    __tablename__ = "ai_inputs"
    __table_args__ = (
        *tenant_args(run_id="ai_runs"),
        UniqueConstraint("workspace_id", "run_id"),
        ForeignKeyConstraint(
            ["workspace_id", "post_id", "revision_id"],
            ["post_revisions.workspace_id", "post_revisions.post_id", "post_revisions.id"],
            name="fk_ai_input_editor_revision",
        ),
        CheckConstraint(
            "(post_id IS NULL) = (revision_id IS NULL) "
            "AND ((post_id IS NULL AND editor_context IS NULL AND copy_context IS NULL) "
            "OR (post_id IS NOT NULL AND ((editor_context IS NULL) <> (copy_context IS NULL))))",
            name="ai_input_content_pair",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "plan_id"],
            ["content_records.workspace_id", "content_records.id"],
            name="fk_ai_input_plan",
        ),
        CheckConstraint(
            "(plan_id IS NULL) = (planner_context IS NULL) AND "
            "(plan_id IS NULL OR (post_id IS NULL AND revision_id IS NULL "
            "AND editor_context IS NULL AND copy_context IS NULL))",
            name="ai_input_planner_pair",
        ),
    )
    run_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    question: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    post_id: Mapped[UUID | None]
    revision_id: Mapped[UUID | None]
    editor_context: Mapped[dict[str, object] | None] = mapped_column(JSON(none_as_null=True))
    copy_context: Mapped[dict[str, object] | None] = mapped_column(JSON(none_as_null=True))
    plan_id: Mapped[UUID | None]
    planner_context: Mapped[dict[str, object] | None] = mapped_column(JSON(none_as_null=True))


class AICancel(Tenant, Base):
    __tablename__ = "ai_cancel_receipts"
    __table_args__ = (
        *tenant_args(run_id="ai_runs"),
        UniqueConstraint("workspace_id", "actor_id", "key_hash"),
    )
    run_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)


class AIArtifact(Tenant, Base):
    __tablename__ = "ai_artifacts"
    __table_args__ = (*tenant_args(run_id="ai_runs"), UniqueConstraint("workspace_id", "run_id"))
    run_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[dict[str, object]] = mapped_column(JSON)
    citation_ids: Mapped[list[str]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))


class PlanNotes(Tenant, Base):
    __tablename__ = "plan_notes"
    __table_args__ = (
        *tenant_args(plan_id="content_records"),
        UniqueConstraint("workspace_id", "plan_id"),
    )
    plan_id: Mapped[UUID]
    plan_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[dict[str, object]] = mapped_column(JSON)


class PlanAdoption(Tenant, Base):
    __tablename__ = "plan_adoptions"
    __table_args__ = (
        *tenant_args(
            run_id="ai_runs",
            artifact_id="ai_artifacts",
            input_id="ai_inputs",
            source_plan_id="content_records",
            plan_id="content_records",
            notes_id="plan_notes",
        ),
        UniqueConstraint("workspace_id", "run_id"),
        UniqueConstraint("workspace_id", "plan_id"),
        UniqueConstraint("workspace_id", "notes_id"),
        UniqueConstraint("workspace_id", "actor_id", "key_hash"),
        CheckConstraint(
            "source_plan_id <> plan_id AND plan_number>=2", name="plan_adoption_version"
        ),
        CheckConstraint(
            "human_confirmed AND share_with_workspace_confirmed", name="plan_adoption_confirmation"
        ),
    )
    run_id: Mapped[UUID]
    artifact_id: Mapped[UUID]
    artifact_hash: Mapped[str] = mapped_column(String(64))
    input_id: Mapped[UUID]
    input_hash: Mapped[str] = mapped_column(String(64))
    source_plan_id: Mapped[UUID]
    source_content_hash: Mapped[str] = mapped_column(String(64))
    plan_id: Mapped[UUID]
    content_hash: Mapped[str] = mapped_column(String(64))
    plan_number: Mapped[int]
    notes_id: Mapped[UUID]
    notes_hash: Mapped[str] = mapped_column(String(64))
    preview_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    human_confirmed: Mapped[bool] = mapped_column(Boolean)
    share_with_workspace_confirmed: Mapped[bool] = mapped_column(Boolean)


class CopyAdoption(Tenant, Base):
    __tablename__ = "copy_adoptions"
    __table_args__ = (
        *tenant_args(
            run_id="ai_runs", artifact_id="ai_artifacts", input_id="ai_inputs", post_id="posts"
        ),
        UniqueConstraint("workspace_id", "run_id"),
        UniqueConstraint("workspace_id", "revision_id"),
        UniqueConstraint("workspace_id", "actor_id", "key_hash"),
        ForeignKeyConstraint(
            ["workspace_id", "post_id", "source_revision_id"],
            ["post_revisions.workspace_id", "post_revisions.post_id", "post_revisions.id"],
        ),
        ForeignKeyConstraint(
            ["workspace_id", "post_id", "revision_id"],
            ["post_revisions.workspace_id", "post_revisions.post_id", "post_revisions.id"],
        ),
        CheckConstraint(
            "source_revision_id <> revision_id AND post_version>=2", name="copy_adoption_revision"
        ),
        CheckConstraint(
            "human_confirmed AND share_with_workspace_confirmed", name="copy_adoption_confirmation"
        ),
    )
    run_id: Mapped[UUID]
    artifact_id: Mapped[UUID]
    artifact_hash: Mapped[str] = mapped_column(String(64))
    input_id: Mapped[UUID]
    input_hash: Mapped[str] = mapped_column(String(64))
    post_id: Mapped[UUID]
    source_revision_id: Mapped[UUID]
    source_content_hash: Mapped[str] = mapped_column(String(64))
    revision_id: Mapped[UUID]
    content_hash: Mapped[str] = mapped_column(String(64))
    post_version: Mapped[int]
    preview_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    preflight: Mapped[dict[str, object]] = mapped_column(JSON)
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    human_confirmed: Mapped[bool] = mapped_column(Boolean)
    share_with_workspace_confirmed: Mapped[bool] = mapped_column(Boolean)


class EditorialDecision(Tenant, Base):
    __tablename__ = "editorial_decisions"
    __table_args__ = (
        *tenant_args(run_id="ai_runs", artifact_id="ai_artifacts", revision_id="post_revisions"),
        UniqueConstraint("workspace_id", "actor_id", "key_hash"),
        UniqueConstraint("workspace_id", "run_id", "sequence"),
        CheckConstraint("sequence>=1", name="editorial_decision_sequence"),
        CheckConstraint("finding_index BETWEEN 0 AND 19", name="editorial_finding_index"),
        CheckConstraint("status IN ('open','needs_changes','dismissed')", name="editorial_status"),
    )
    run_id: Mapped[UUID]
    artifact_id: Mapped[UUID]
    artifact_hash: Mapped[str] = mapped_column(String(64))
    revision_id: Mapped[UUID]
    content_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    finding_index: Mapped[int]
    finding_hash: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int]
    status: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
