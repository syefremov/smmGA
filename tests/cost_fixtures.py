"""Synthetic conservative rates only. Never real model prices or paid-call permission."""

from datetime import UTC, datetime

from smm_gpt.domain.ai_costs import CostPolicy


def policy() -> CostPolicy:
    return CostPolicy(
        version="synthetic-v1",
        model="synthetic-model",
        input_rate_microusd_per_million=2_000_000,
        output_rate_microusd_per_million=8_000_000,
        reserve_microusd=10_000,
        workspace_limit_microusd=1_000_000,
        valid_until=datetime(2099, 1, 1, tzinfo=UTC),
    )
