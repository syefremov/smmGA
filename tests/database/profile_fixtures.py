"""Synthetic Owner selection; never enables a real provider or calls a model."""

from uuid import uuid4

from smm_gpt.domain.ai import ProfileName
from smm_gpt.domain.profiles import DraftProfile, ProfileReceipt, SelectTesting
from smm_gpt.services.profiles import ProfileService

from .conftest import TenantFixture


async def select_profile(
    t: TenantFixture,
    name: ProfileName = "product_expert",
    purpose: str = "Synthetic reference assessment",
) -> ProfileReceipt:
    core = ProfileService(t.access)
    draft = await core.execute(
        t.owner,
        t.workspace,
        DraftProfile(
            idempotency_key=uuid4().hex,
            profile=name,
            expected_revision=0,
            purpose=purpose,
            model="synthetic-model",
            reason="Synthetic fixture only",
        ),
        uuid4(),
    )
    version = await core.read_version(t.owner, t.workspace, draft.version_id, uuid4())
    return await core.execute(
        t.owner,
        t.workspace,
        SelectTesting(
            idempotency_key=uuid4().hex,
            profile=name,
            expected_revision=draft.revision,
            version_id=draft.version_id,
            content_hash=version.content_hash,
            reason="Synthetic human testing decision",
            human_confirmed=True,
        ),
        uuid4(),
    )
