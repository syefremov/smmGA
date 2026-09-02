"""Actor-private run reservations and append-only AI outputs."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from smm_gpt.infrastructure.content_models import Tenant, tenant_args
from smm_gpt.infrastructure.database import Base


class AIRun(Tenant, Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        *tenant_args(brand_id="brands", retrieval_run_id="retrieval_runs"),
        UniqueConstraint("workspace_id", "actor_id", "key_hash"),
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    brand_id: Mapped[UUID]
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    profile: Mapped[str] = mapped_column(String(32))
    profile_version: Mapped[str] = mapped_column(String(80))
    profile_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
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
    __table_args__ = (*tenant_args(run_id="ai_runs"), UniqueConstraint("workspace_id", "run_id"))
    run_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    question: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))


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
