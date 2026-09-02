"""Central identity and tenant records. Database policies live in Alembic migrations."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from smm_gpt.infrastructure.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Record:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Record, Base):
    __tablename__ = "users"
    display_name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Identity(Record, Base):
    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Workspace(Record, Base):
    __tablename__ = "workspaces"
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(80), default="Europe/Moscow")


class Membership(Record, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id"),
        CheckConstraint(
            "role IN ('owner','administrator','strategist','editor',"
            "'publisher','analyst','viewer')",
            name="membership_role",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(24))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginFlow(Record, Base):
    __tablename__ = "login_flows"
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WebSession(Record, Base):
    __tablename__ = "web_sessions"
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("user_identities.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    mfa: Mapped[bool] = mapped_column(Boolean)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Record, Base):
    __tablename__ = "audit_events"
    workspace_id: Mapped[UUID | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[UUID | None]
    outcome: Mapped[str] = mapped_column(String(24))
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class Job(Record, Base):
    __tablename__ = "system_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint("state IN ('pending','running','succeeded','failed')", name="job_state"),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(24), default="pending")


class FileMetadata(Record, Base):
    __tablename__ = "file_metadata"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "storage_key"),
        CheckConstraint("size_bytes >= 0", name="file_size"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"], ["system_jobs.workspace_id", "system_jobs.id"]
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    job_id: Mapped[UUID | None]
    storage_key: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)


class IdempotencyKey(Record, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("workspace_id", "actor_id", "operation", "key_hash"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"], ["system_jobs.workspace_id", "system_jobs.id"]
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(80))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[UUID]


class OutboxEvent(Record, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "job_id", "kind"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"], ["system_jobs.workspace_id", "system_jobs.id"]
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    job_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(80))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
