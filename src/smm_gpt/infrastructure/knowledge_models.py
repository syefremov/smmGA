"""Small text originals live in PostgreSQL; no paths or external URLs are fetched."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from smm_gpt.infrastructure.content_models import Tenant, tenant_args
from smm_gpt.infrastructure.database import Base


class KnowledgeDocument(Tenant, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        *tenant_args(brand_id="brands"),
        ForeignKeyConstraint(
            ["workspace_id", "id", "active_index_id"],
            [
                "knowledge_indexes.workspace_id",
                "knowledge_indexes.document_id",
                "knowledge_indexes.id",
            ],
            use_alter=True,
            name="fk_knowledge_active_index",
        ),
        CheckConstraint("visibility IN ('workspace','owner')", name="knowledge_visibility"),
    )
    brand_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(200))
    document_type: Mapped[str] = mapped_column(String(32))
    visibility: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    active_index_id: Mapped[UUID | None]


class KnowledgeVersion(Tenant, Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        *tenant_args(document_id="knowledge_documents"),
        ForeignKeyConstraint(
            ["workspace_id", "source_file_id"],
            ["knowledge_files.workspace_id", "knowledge_files.id"],
            name="fk_knowledge_source_file",
        ),
        UniqueConstraint("workspace_id", "document_id", "id"),
        UniqueConstraint("workspace_id", "document_id", "fingerprint"),
    )
    document_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    source_file_id: Mapped[UUID | None]
    original: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(24))
    fingerprint: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    source_uri: Mapped[str] = mapped_column(String(1000))
    source_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeIndex(Tenant, Base):
    __tablename__ = "knowledge_indexes"
    __table_args__ = (
        *tenant_args(document_id="knowledge_documents"),
        UniqueConstraint("workspace_id", "document_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "document_id", "document_version_id"],
            [
                "knowledge_document_versions.workspace_id",
                "knowledge_document_versions.document_id",
                "knowledge_document_versions.id",
            ],
        ),
        CheckConstraint(
            "state IN ('queued','processing','ready','failed')", name="knowledge_index_state"
        ),
    )
    document_id: Mapped[UUID]
    document_version_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("user_identities.id"))
    state: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    parser_version: Mapped[str] = mapped_column(String(80), default="safe-text-v1")
    chunking_version: Mapped[str] = mapped_column(String(80), default="paragraph-v1")
    content_hash: Mapped[str] = mapped_column(String(64))


class KnowledgeChunk(Tenant, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        *tenant_args(document_id="knowledge_documents"),
        ForeignKeyConstraint(
            ["workspace_id", "document_id", "index_id"],
            [
                "knowledge_indexes.workspace_id",
                "knowledge_indexes.document_id",
                "knowledge_indexes.id",
            ],
        ),
        UniqueConstraint("workspace_id", "index_id", "ordinal"),
        Index("ix_knowledge_chunks_search", "search_vector", postgresql_using="gin"),
    )
    document_id: Mapped[UUID]
    index_id: Mapped[UUID]
    ordinal: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('russian', search_text) || to_tsvector('simple', search_text)",
            persisted=True,
        ),
    )


class KnowledgeReceipt(Tenant, Base):
    __tablename__ = "knowledge_receipts"
    __table_args__ = (*tenant_args(), UniqueConstraint("workspace_id", "actor_id", "key_hash"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)


class RetrievalRun(Tenant, Base):
    __tablename__ = "retrieval_runs"
    __table_args__ = tenant_args(brand_id="brands")
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    brand_id: Mapped[UUID]
    query_hash: Mapped[str] = mapped_column(String(64))
    algorithm: Mapped[str] = mapped_column(String(80))
    chunk_ids: Mapped[list[str]] = mapped_column(JSON)


class KnowledgeNote(Tenant, Base):
    __tablename__ = "knowledge_notes"
    __table_args__ = tenant_args(brand_id="brands")
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    brand_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    safe_alternative: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)
    effective_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeNoteReview(Tenant, Base):
    __tablename__ = "knowledge_note_reviews"
    __table_args__ = (
        *tenant_args(note_id="knowledge_notes"),
        UniqueConstraint("workspace_id", "note_id"),
    )
    note_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)


class KnowledgeActivation(Tenant, Base):
    __tablename__ = "knowledge_activations"
    __table_args__ = tenant_args(index_id="knowledge_indexes")
    index_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    query_hashes: Mapped[list[str]] = mapped_column(JSON)
