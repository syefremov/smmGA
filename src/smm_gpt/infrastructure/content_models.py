"""Tenant-scoped content persistence; typed JSON bodies are validated by domain contracts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Constraint

from smm_gpt.infrastructure.database import Base
from smm_gpt.infrastructure.models import Record


def tenant_args(**links: str) -> tuple[Constraint, ...]:
    return (
        UniqueConstraint("workspace_id", "id"),
        *(
            ForeignKeyConstraint(
                ["workspace_id", column], [f"{target}.workspace_id", f"{target}.id"]
            )
            for column, target in links.items()
        ),
    )


class Tenant(Record):
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)


class ContentRecord(Tenant, Base):
    __tablename__ = "content_records"
    __table_args__ = (
        *tenant_args(brand_id="brands", source_id="sources", product_id="products"),
        UniqueConstraint("workspace_id", "family_id", "number"),
        CheckConstraint("number >= 1", name="content_record_number"),
    )
    brand_id: Mapped[UUID]
    source_id: Mapped[UUID | None]
    product_id: Mapped[UUID | None]
    family_id: Mapped[UUID]
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    body: Mapped[dict[str, object]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContentLink(Tenant, Base):
    __tablename__ = "content_links"
    __table_args__ = (
        *tenant_args(record_id="content_records", target_id="content_records"),
        UniqueConstraint("workspace_id", "record_id", "target_id"),
    )
    record_id: Mapped[UUID]
    target_id: Mapped[UUID]


class Post(Tenant, Base):
    __tablename__ = "posts"
    __table_args__ = (
        *tenant_args(brand_id="brands", brief_id="content_records", idea_id="content_records"),
        CheckConstraint(
            "state IN ('draft','in_review','rejected','approved','package_ready')",
            name="post_state",
        ),
        CheckConstraint("version >= 1 AND revision_count >= 0", name="post_version"),
        ForeignKeyConstraint(
            ["workspace_id", "id", "current_revision_id"],
            ["post_revisions.workspace_id", "post_revisions.post_id", "post_revisions.id"],
            name="fk_post_current_revision",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["workspace_id", "id", "active_approval_id"],
            ["content_decisions.workspace_id", "content_decisions.post_id", "content_decisions.id"],
            name="fk_post_active_approval",
            use_alter=True,
        ),
    )
    brand_id: Mapped[UUID]
    brief_id: Mapped[UUID]
    idea_id: Mapped[UUID | None]
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(24), default="draft")
    current_revision_id: Mapped[UUID | None]
    active_approval_id: Mapped[UUID | None]


class PostRevision(Tenant, Base):
    __tablename__ = "post_revisions"
    __table_args__ = (
        *tenant_args(post_id="posts"),
        UniqueConstraint("workspace_id", "post_id", "number"),
        UniqueConstraint("workspace_id", "post_id", "id"),
    )
    post_id: Mapped[UUID]
    number: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[dict[str, object]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    media_manifest: Mapped[list[dict[str, object]]] = mapped_column(JSON)


def revision_args() -> tuple[Constraint, ...]:
    return (
        *tenant_args(post_id="posts"),
        ForeignKeyConstraint(
            ["workspace_id", "post_id", "revision_id"],
            ["post_revisions.workspace_id", "post_revisions.post_id", "post_revisions.id"],
        ),
    )


class ContentDecision(Tenant, Base):
    __tablename__ = "content_decisions"
    __table_args__ = (
        *revision_args(),
        UniqueConstraint("workspace_id", "post_id", "id"),
        CheckConstraint("decision IN ('approve','reject')", name="content_decision"),
    )
    post_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    preflight: Mapped[dict[str, object]] = mapped_column(JSON)


class ContentComment(Tenant, Base):
    __tablename__ = "content_comments"
    __table_args__ = revision_args()
    post_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)


class ReviewRun(Tenant, Base):
    __tablename__ = "content_review_runs"
    __table_args__ = revision_args()
    post_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    result: Mapped[dict[str, object]] = mapped_column(JSON)


class WorkingCopy(Tenant, Base):
    __tablename__ = "post_working_copies"
    __table_args__ = (
        *tenant_args(post_id="posts"),
        UniqueConstraint("workspace_id", "post_id", "actor_id"),
    )
    post_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer)
    base_version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, object]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationPackage(Tenant, Base):
    __tablename__ = "publication_packages"
    __table_args__ = (
        *revision_args(),
        ForeignKeyConstraint(
            ["workspace_id", "post_id", "approval_id"],
            ["content_decisions.workspace_id", "content_decisions.post_id", "content_decisions.id"],
        ),
    )
    post_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    approval_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80))
    manifest: Mapped[dict[str, object]] = mapped_column(JSON)


class PackageCancellation(Tenant, Base):
    __tablename__ = "package_cancellations"
    __table_args__ = (
        *tenant_args(package_id="publication_packages"),
        UniqueConstraint("workspace_id", "package_id"),
    )
    package_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class ContentReceipt(Tenant, Base):
    __tablename__ = "content_receipts"
    __table_args__ = (*tenant_args(), UniqueConstraint("workspace_id", "actor_id", "key_hash"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)


class WorkDependency(Tenant, Base):
    __tablename__ = "work_item_dependencies"
    __table_args__ = (
        *tenant_args(item_id="work_items", depends_on="work_items"),
        UniqueConstraint("workspace_id", "item_id", "depends_on"),
        CheckConstraint("item_id <> depends_on", name="work_dependency_self"),
    )
    item_id: Mapped[UUID]
    depends_on: Mapped[UUID]
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkAssignment(Tenant, Base):
    __tablename__ = "work_assignments"
    __table_args__ = (
        *tenant_args(item_id="work_items", campaign_id="content_records"),
        UniqueConstraint("workspace_id", "item_id"),
        ForeignKeyConstraint(
            ["workspace_id", "assignee_id"], ["memberships.workspace_id", "memberships.user_id"]
        ),
    )
    item_id: Mapped[UUID]
    campaign_id: Mapped[UUID | None]
    assignee_id: Mapped[UUID]
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
