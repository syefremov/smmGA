from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from smm_gpt.core.config import Settings
from smm_gpt.domain.ai_costs import CostPolicy
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.ai_costs import configured, estimate
from smm_gpt.services.model_gateway import Usage

from .cost_fixtures import policy


def test_integer_estimate_rounds_up_and_is_not_an_invoice() -> None:
    assert estimate(policy(), 20, 10) == 120
    p = policy().model_copy(
        update={"input_rate_microusd_per_million": 1, "output_rate_microusd_per_million": 1}
    )
    assert estimate(p, 1, 0) == 1
    assert estimate(p, 0, 0) == 0
    assert estimate(p, 1_000_000, 1_000_000) == 2


@pytest.mark.parametrize("tokens", [-1, True, 1.5, 1_000_000_001])
def test_usage_limits(tokens: int) -> None:
    with pytest.raises(OperationError, match="model_usage_invalid"):
        estimate(policy(), tokens, 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reserve_microusd", 0),
        ("reserve_microusd", True),
        ("workspace_limit_microusd", 1),
        ("input_rate_microusd_per_million", "2"),
        ("currency", "EUR"),
        ("valid_until", datetime(2099, 1, 1)),
        ("model", "untrusted model\n"),
    ],
)
def test_policy_has_no_float_currency_or_timezone_ambiguity(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CostPolicy.model_validate({**policy().model_dump(), field: value})


def test_no_default_price_or_spending_enablement() -> None:
    settings = Settings(_env_file=None)
    assert settings.ai_cost_policy is None and settings.ai_provider == "disabled"
    with pytest.raises(OperationError, match="ai_budget_not_configured"):
        configured(settings)
    settings.ai_cost_policy = policy()
    with pytest.raises(OperationError, match="ai_price_policy_unavailable"):
        configured(settings)
    settings.ai_model = "synthetic-model"
    assert configured(settings) == policy()
    settings.ai_cost_policy = policy().model_copy(
        update={"valid_until": utcnow() - timedelta(seconds=1)}
    )
    with pytest.raises(OperationError, match="ai_price_policy_unavailable"):
        configured(settings)
    assert settings.ai_provider == "disabled" and not settings.ai_worker_enabled


@pytest.mark.parametrize("value", [True, "20", 1.0, -1, 1_000_000_001, None])
def test_provider_usage_cannot_silently_coerce_malformed_counts(value: object) -> None:
    with pytest.raises(ValidationError):
        Usage.model_validate({"input_tokens": value, "output_tokens": 10})
