"""Append-only actor-private budget snapshots; only bounded totals cross actor boundaries."""

from uuid import UUID

from sqlalchemy import JSON, BigInteger, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from smm_gpt.infrastructure.content_models import Tenant, tenant_args
from smm_gpt.infrastructure.database import Base


class AICostReservation(Tenant, Base):
    __tablename__ = "ai_cost_reservations"
    __table_args__ = (
        *tenant_args(run_id="ai_runs"),
        UniqueConstraint("workspace_id", "run_id"),
        CheckConstraint(
            "reserved_microusd > 0 AND reserved_microusd <= 1000000000", name="cost_reserve_amount"
        ),
    )
    run_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    input_hash: Mapped[str] = mapped_column(String(64))
    policy_hash: Mapped[str] = mapped_column(String(64))
    policy: Mapped[dict[str, object]] = mapped_column(JSON)
    reserved_microusd: Mapped[int] = mapped_column(BigInteger)


class AICostObservation(Tenant, Base):
    __tablename__ = "ai_cost_observations"
    __table_args__ = (
        *tenant_args(run_id="ai_runs"),
        UniqueConstraint("workspace_id", "run_id"),
        CheckConstraint(
            "input_tokens BETWEEN 0 AND 1000000000 AND output_tokens BETWEEN 0 AND 1000000000 "
            "AND estimated_microusd >= 0",
            name="cost_usage_amount",
        ),
    )
    run_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    lease_id: Mapped[UUID]
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    estimated_microusd: Mapped[int] = mapped_column(BigInteger)
    model: Mapped[str] = mapped_column(String(120))
    response_id: Mapped[str] = mapped_column(String(160))
