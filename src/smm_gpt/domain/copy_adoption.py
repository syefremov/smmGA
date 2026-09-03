"""Personal commands for an exact, separately confirmed transfer into shared content."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from smm_gpt.domain.content import Hash, Preflight, RevisionBody
from smm_gpt.domain.copywriter import CopyDraft
from smm_gpt.domain.knowledge import ShortText
from smm_gpt.domain.operations import DTO, IdempotencyToken


class CopyAdoptionPreview(DTO):
    run_id: UUID
    artifact_id: UUID
    artifact_hash: Hash
    input_id: UUID
    input_hash: Hash
    post_id: UUID
    post_version: int
    source_revision_id: UUID
    source_content_hash: Hash
    proposed_content_hash: Hash
    preview_hash: Hash
    body: RevisionBody
    draft: CopyDraft
    warning: str = (
        "Это предпросмотр, не запись и не одобрение. После отдельного подтверждения текст, "
        "fact IDs и пробелы станут доступны участникам, которым доступен контент workspace. "
        "Старое согласование будет снято; рабочие копии сохранятся."
    )


class AdoptCopyDraft(DTO):
    idempotency_key: IdempotencyToken
    artifact_id: UUID
    artifact_hash: Hash
    preview_hash: Hash
    proposed_content_hash: Hash
    expected_post_version: Annotated[int, Field(ge=1, strict=True)]
    reason: ShortText
    human_confirmed: Literal[True]
    share_with_workspace_confirmed: Literal[True]


class CopyAdoptionView(DTO):
    id: UUID
    run_id: UUID
    artifact_id: UUID
    artifact_hash: Hash
    input_id: UUID
    input_hash: Hash
    post_id: UUID
    source_revision_id: UUID
    source_content_hash: Hash
    revision_id: UUID
    content_hash: Hash
    post_version: int
    preview_hash: Hash
    actor_id: UUID
    created_at: datetime
    reason: str
    preflight: Preflight
    historical_only: Literal[True] = True
    warning: str = (
        "История сохранения, не текущий статус или одобрение. "
        "Перечитайте пост и выполните актуальную проверку перед решением."
    )
