"""Private immutable upload metadata + fenced queue, separately immutable extraction."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from smm_gpt.infrastructure.content_models import Tenant, tenant_args
from smm_gpt.infrastructure.database import Base


class KnowledgeFile(Tenant, Base):
    __tablename__ = "knowledge_files"
    __table_args__ = (
        *tenant_args(brand_id="brands"),
        CheckConstraint("byte_size > 0 AND byte_size <= 2097152", name="knowledge_file_size"),
        CheckConstraint(
            "state IN ('queued','processing','ready','failed','cancelled')",
            name="knowledge_file_state",
        ),
        UniqueConstraint("workspace_id", "actor_id", "key_hash"),
    )
    brand_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("user_identities.id"))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(160))
    format: Mapped[str] = mapped_column(String(8))
    byte_size: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeExtraction(Tenant, Base):
    __tablename__ = "knowledge_extractions"
    __table_args__ = (
        *tenant_args(file_id="knowledge_files"),
        UniqueConstraint("workspace_id", "file_id"),
    )
    file_id: Mapped[UUID]
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(100))
    scan_engine: Mapped[str] = mapped_column(String(100))
    signature_version: Mapped[str] = mapped_column(String(32))
    signatures_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FileRetryReceipt(Tenant, Base):
    __tablename__ = "knowledge_file_retry_receipts"
    __table_args__ = (
        *tenant_args(file_id="knowledge_files"),
        UniqueConstraint("workspace_id", "actor_id", "key_hash"),
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    file_id: Mapped[UUID]
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)
