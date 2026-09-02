"""Append-only benchmark definitions, snapshot reports, owner decisions and receipts."""

from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from smm_gpt.infrastructure.content_models import Tenant, tenant_args
from smm_gpt.infrastructure.database import Base


class EvalDataset(Tenant, Base):
    __tablename__ = "retrieval_eval_datasets"
    __table_args__ = (
        *tenant_args(brand_id="brands", previous_dataset_id="retrieval_eval_datasets"),
        UniqueConstraint("workspace_id", "family_id", "number"),
        UniqueConstraint("workspace_id", "brand_id", "id", "content_hash"),
        UniqueConstraint("workspace_id", "brand_id", "family_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "brand_id", "family_id", "previous_dataset_id"],
            [
                f"retrieval_eval_datasets.{x}"
                for x in ("workspace_id", "brand_id", "family_id", "id")
            ],
        ),
        CheckConstraint("number >= 1", name="retrieval_eval_dataset_number"),
    )
    brand_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    family_id: Mapped[UUID]
    number: Mapped[int] = mapped_column(Integer)
    previous_dataset_id: Mapped[UUID | None]
    content_hash: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, object]] = mapped_column(JSON)


class EvalRun(Tenant, Base):
    __tablename__ = "retrieval_eval_runs"
    __table_args__ = (
        *tenant_args(brand_id="brands", dataset_id="retrieval_eval_datasets"),
        ForeignKeyConstraint(
            ["workspace_id", "brand_id", "dataset_id", "dataset_hash"],
            [
                f"retrieval_eval_datasets.{x}"
                for x in ("workspace_id", "brand_id", "id", "content_hash")
            ],
        ),
        UniqueConstraint("workspace_id", "id", "report_hash"),
    )
    brand_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    dataset_id: Mapped[UUID]
    dataset_hash: Mapped[str] = mapped_column(String(64))
    corpus_hash: Mapped[str] = mapped_column(String(64))
    report_hash: Mapped[str] = mapped_column(String(64))
    corpus: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    report: Mapped[dict[str, object]] = mapped_column(JSON)


class EvalReview(Tenant, Base):
    __tablename__ = "retrieval_eval_reviews"
    __table_args__ = (
        *tenant_args(run_id="retrieval_eval_runs"),
        UniqueConstraint("workspace_id", "run_id"),
        ForeignKeyConstraint(
            ["workspace_id", "run_id", "report_hash"],
            [
                "retrieval_eval_runs.workspace_id",
                "retrieval_eval_runs.id",
                "retrieval_eval_runs.report_hash",
            ],
        ),
        CheckConstraint("decision IN ('accept_baseline','reject')", name="retrieval_eval_decision"),
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[UUID]
    report_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)


class EvalReceipt(Tenant, Base):
    __tablename__ = "retrieval_eval_receipts"
    __table_args__ = (*tenant_args(), UniqueConstraint("workspace_id", "actor_id", "key_hash"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)
