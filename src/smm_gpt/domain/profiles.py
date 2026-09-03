"""Owner-governed testing configurations. No production activation or arbitrary capabilities."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from smm_gpt.domain.ai import ProfileName
from smm_gpt.domain.knowledge import ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ModelName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")]


class DraftProfile(DTO):
    action: Literal["profile_draft"] = "profile_draft"
    idempotency_key: IdempotencyToken
    profile: ProfileName
    expected_revision: Annotated[int, Field(ge=0)]
    purpose: ShortText
    provider: Literal["openai"] = "openai"
    model: ModelName
    reason: ShortText


class ProfileDecision(DTO):
    idempotency_key: IdempotencyToken
    profile: ProfileName
    expected_revision: Annotated[int, Field(ge=1)]
    version_id: UUID
    content_hash: Hash
    reason: ShortText
    human_confirmed: Literal[True]


class SelectTesting(ProfileDecision):
    action: Literal["profile_select_testing"] = "profile_select_testing"


class DisableProfile(ProfileDecision):
    action: Literal["profile_disable"] = "profile_disable"


ProfileCommand = Annotated[
    DraftProfile | SelectTesting | DisableProfile, Field(discriminator="action")
]


class ProfileReceipt(DTO):
    profile: ProfileName
    revision: int
    version_id: UUID
    decision_id: UUID | None = None


class ProfileVersionView(DTO):
    id: UUID
    profile: ProfileName
    number: int
    actor_id: UUID
    provider: str
    model: str
    profile_snapshot: dict[str, object]
    execution_hash: str
    content_hash: str
    reason: str
    created_at: datetime
    compatible: bool = False
    blocked_reason: str | None = None
    warning: str = "Immutable testing configuration, not evaluated production authority."


class ProfileDecisionView(DTO):
    id: UUID
    actor_id: UUID
    action: Literal["profile_select_testing", "profile_disable"]
    version_id: UUID
    revision: int
    content_hash: str
    reason: str
    created_at: datetime


class RegisteredProfile(DTO):
    profile: ProfileName
    revision: int
    latest_version_id: UUID
    testing_version_id: UUID | None
    testing_selection_id: UUID | None


class ProfileDetail(RegisteredProfile):
    latest: ProfileVersionView
    testing: ProfileVersionView | None
    versions: list[ProfileVersionView]
    decisions: list[ProfileDecisionView]
    versions_truncated: bool
    decisions_truncated: bool
    warning: str = (
        "Testing selection does not enable a provider, authorize spending or approve content."
    )
