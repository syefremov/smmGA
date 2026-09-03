"""Shared transactional revision write. Callers authorize and lock the post first."""

from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import content as d
from smm_gpt.infrastructure.content_models import Post, PostRevision, WorkingCopy
from smm_gpt.services.content_preflight import media_manifest
from smm_gpt.services.content_records import record


async def save_revision(
    s: AsyncSession,
    post: Post,
    actor_id: UUID,
    body: d.RevisionBody,
    *,
    clear_working_copy: bool = False,
) -> PostRevision:
    # Manual saves permit unconfirmed facts; preflight and approval enforce freshness separately.
    for rid in body.fact_ids:
        await record(s, post.workspace_id, rid, "product_fact", post.brand_id)
    manifest = await media_manifest(s, post.workspace_id, body)
    payload = body.model_dump(mode="json")
    revision = PostRevision(
        id=uuid4(),
        workspace_id=post.workspace_id,
        post_id=post.id,
        number=post.revision_count + 1,
        actor_id=actor_id,
        body=payload,
        media_manifest=manifest,
        content_hash=d.canonical_hash({"body": payload, "media_manifest": manifest}),
    )
    s.add(revision)
    await s.flush()
    post.current_revision_id = revision.id
    post.revision_count += 1
    post.version += 1
    post.state = "draft"
    post.active_approval_id = None
    if clear_working_copy:
        await s.execute(
            delete(WorkingCopy).where(
                WorkingCopy.workspace_id == post.workspace_id,
                WorkingCopy.post_id == post.id,
                WorkingCopy.actor_id == actor_id,
            )
        )
    return revision
