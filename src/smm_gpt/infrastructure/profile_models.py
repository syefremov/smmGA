"""Immutable definitions and decisions, with an Owner-managed testing selection."""

from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from smm_gpt.infrastructure.content_models import Tenant, tenant_args
from smm_gpt.infrastructure.database import Base


class AIProfileVersion(Tenant, Base):
    __tablename__ = "ai_profile_versions"
    __table_args__ = (
        *tenant_args(),
        UniqueConstraint("workspace_id", "profile", "id"),
        UniqueConstraint("workspace_id", "profile", "number"),
        CheckConstraint("number >= 1", name="ai_profile_number"),
    )
    profile: Mapped[str] = mapped_column(String(32))
    number: Mapped[int]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    profile_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    execution_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)


class AIProfileDecision(Tenant, Base):
    __tablename__ = "ai_profile_decisions"
    __table_args__ = (
        *tenant_args(),
        UniqueConstraint("workspace_id", "profile", "version_id", "id"),
        UniqueConstraint("workspace_id", "profile", "revision"),
        ForeignKeyConstraint(
            ["workspace_id", "profile", "version_id"],
            [
                "ai_profile_versions.workspace_id",
                "ai_profile_versions.profile",
                "ai_profile_versions.id",
            ],
        ),
        CheckConstraint(
            "action IN ('profile_select_testing','profile_disable')",
            name="ai_profile_decision_action",
        ),
        CheckConstraint("revision >= 2", name="ai_profile_decision_revision"),
    )
    profile: Mapped[str] = mapped_column(String(32))
    version_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)


class AIProfileHead(Tenant, Base):
    __tablename__ = "ai_profile_heads"
    __table_args__ = (
        *tenant_args(),
        UniqueConstraint("workspace_id", "profile"),
        CheckConstraint("revision >= 1", name="ai_profile_revision"),
        CheckConstraint(
            "(testing_version_id IS NULL) = (testing_selection_id IS NULL)",
            name="ai_profile_selection_pair",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "profile", "latest_version_id"],
            [
                "ai_profile_versions.workspace_id",
                "ai_profile_versions.profile",
                "ai_profile_versions.id",
            ],
            name="fk_profile_latest",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "profile", "testing_version_id", "testing_selection_id"],
            [
                "ai_profile_decisions.workspace_id",
                "ai_profile_decisions.profile",
                "ai_profile_decisions.version_id",
                "ai_profile_decisions.id",
            ],
            name="fk_profile_selection",
        ),
    )
    profile: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int]
    latest_version_id: Mapped[UUID]
    testing_version_id: Mapped[UUID | None]
    testing_selection_id: Mapped[UUID | None]


class AIProfileReceipt(Tenant, Base):
    __tablename__ = "ai_profile_receipts"
    __table_args__ = (*tenant_args(), UniqueConstraint("workspace_id", "actor_id", "key_hash"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)
