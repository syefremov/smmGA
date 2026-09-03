"""Conservative internal reservations, never a provider invoice or spending authorization."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from smm_gpt.domain.operations import DTO

PositiveAmount = Annotated[StrictInt, Field(ge=1, le=1_000_000_000)]


class CostPolicy(DTO):
    version: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._-]+$")
    model: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9._-]+$")
    currency: Literal["USD"] = "USD"
    input_rate_microusd_per_million: PositiveAmount
    output_rate_microusd_per_million: PositiveAmount
    reserve_microusd: PositiveAmount
    workspace_limit_microusd: Annotated[StrictInt, Field(ge=1, le=1_000_000_000_000)]
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def utc_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Explicit timezone required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def limits(self) -> "CostPolicy":
        if self.reserve_microusd > self.workspace_limit_microusd:
            raise ValueError("Reservation exceeds workspace limit")
        return self


class CostSummary(DTO):
    currency: Literal["USD"] = "USD"
    policy: CostPolicy | None
    reserved_microusd: int
    estimated_microusd: int
    unresolved_runs: int
    overrun_runs: int
    in_flight_runs: int
    available_microusd: int
    warning: str = (
        "Internal lifetime reservations and tariff estimates, not a provider invoice. "
        "Reservations are not automatically refunded. Unknown spend requires owner reconciliation."
    )


class CostObservationView(DTO):
    created_at: datetime
    input_tokens: int
    output_tokens: int
    estimated_microusd: int
    model: str
    response_id: str


class CostReceipt(DTO):
    run_id: UUID
    created_at: datetime
    input_hash: str
    policy: CostPolicy
    policy_hash: str
    reserved_microusd: int
    observation: CostObservationView | None
    warning: str = "Historical accounting only; not current approval or a paid-call retry permit."
