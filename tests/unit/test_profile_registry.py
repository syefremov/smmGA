from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from smm_gpt.domain import profiles as d
from smm_gpt.domain.ai import PROFILES
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.infrastructure.profile_models import AIProfileVersion
from smm_gpt.services.profiles import compatible_profile, execution_hash, version_hash, version_view


def draft() -> d.DraftProfile:
    return d.DraftProfile(
        idempotency_key=uuid4().hex,
        profile="product_expert",
        expected_revision=0,
        purpose="Synthetic purpose",
        model="synthetic-model",
        reason="Fixture",
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"allowed_capabilities": ["publish"]},
        {"tools": ["network.fetch"]},
        {"output_schema": "Approval"},
        {"status": "active"},
        {"provider": "custom"},
        {"model": "https://example.invalid"},
        {"profile": "orchestrator"},
        {"purpose": " "},
        {"purpose": "x" * 2001},
        {"expected_revision": -1},
        {"profile_snapshot": {"human_approved": True}},
    ],
)
def test_draft_contract_does_not_accept_capability_changes(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(d.ProfileCommand).validate_python(draft().model_dump() | patch)


@pytest.mark.parametrize("action", ["profile_select_testing", "profile_disable"])
def test_selection_requires_exact_confirmation_and_hash(action: str) -> None:
    value = dict(
        action=action,
        idempotency_key=uuid4().hex,
        profile="product_expert",
        expected_revision=1,
        version_id=uuid4(),
        content_hash="a" * 64,
        reason="Synthetic choice",
        human_confirmed=True,
    )
    parsed: d.ProfileCommand = TypeAdapter(d.ProfileCommand).validate_python(value)
    assert parsed.action == action
    patches: list[dict[str, object]] = [
        {"human_confirmed": False},
        {"content_hash": "wrong"},
        {"expected_revision": 0},
    ]
    for patch in patches:
        with pytest.raises(ValidationError):
            TypeAdapter(d.ProfileCommand).validate_python(value | patch)


def test_execution_hash_binds_model_purpose_schema_and_capabilities() -> None:
    profile = PROFILES[0]
    original = execution_hash(profile, "openai", "synthetic-model")
    assert original == execution_hash(profile, "openai", "synthetic-model")
    assert original != execution_hash(profile, "openai", "other-model")
    patches: list[dict[str, object]] = [
        {"purpose": "Changed"},
        {"allowed_capabilities": ["publish"]},
    ]
    for patch in patches:
        assert original != execution_hash(
            profile.model_copy(update=patch), "openai", "synthetic-model"
        )


def test_version_hash_and_code_owned_contract_cannot_be_forged() -> None:
    profile = PROFILES[0].model_copy(update={"purpose": "Synthetic custom scope"})
    row = AIProfileVersion(
        id=uuid4(),
        workspace_id=uuid4(),
        actor_id=uuid4(),
        profile=profile.name,
        number=1,
        created_at=utcnow(),
        provider="openai",
        model="synthetic-model",
        profile_snapshot=profile.model_dump(mode="json"),
        execution_hash=execution_hash(profile, "openai", "synthetic-model"),
        reason="Fixture",
    )
    row.content_hash = version_hash(row)
    assert compatible_profile(row) == profile
    forged = profile.model_copy(update={"allowed_capabilities": ["publish"]})
    row.profile_snapshot = forged.model_dump(mode="json")
    row.execution_hash = execution_hash(forged, "openai", "synthetic-model")
    # Even recalculating data hashes does not make an unimplemented capability executable.
    row.content_hash = version_hash(row)
    with pytest.raises(OperationError, match="profile_contract_changed"):
        compatible_profile(row)
    assert not version_view(row).compatible
