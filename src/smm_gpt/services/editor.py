"""Read-only SQL grounding and deterministic validation, shared by chat and worker."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import content as c
from smm_gpt.domain.editor import EditorContext, EditorialReview
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.content_models import Post, PostRevision
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.content_preflight import preflight
from smm_gpt.services.content_records import current, record, workspace_lock
from smm_gpt.services.knowledge_text import safe_text


async def snapshot(
    s: AsyncSession,
    wid: UUID,
    brand: UUID,
    pid: UUID,
    rid: UUID,
    content_hash: str,
    *,
    require_latest: bool = True,
) -> EditorContext:
    # Caller takes knowledge lock FIRST. Content writers only take this second lock.
    await workspace_lock(s, wid)
    post = await s.scalar(select(Post).where(Post.workspace_id == wid, Post.id == pid))
    revision = await s.scalar(
        select(PostRevision).where(
            PostRevision.workspace_id == wid,
            PostRevision.post_id == pid,
            PostRevision.id == rid,
        )
    )
    if post is None or revision is None or post.brand_id != brand:
        raise OperationError("editor_revision_unavailable")
    if (
        require_latest and post.current_revision_id != rid
    ) or revision.content_hash != content_hash:
        raise OperationError("editor_revision_changed")
    if (
        c.canonical_hash({"body": revision.body, "media_manifest": revision.media_manifest})
        != content_hash
    ):
        raise OperationError("editor_revision_integrity_error")
    brief = await record(s, wid, post.brief_id, "brief", brand)
    if brief.expires_at <= utcnow() or c.canonical_hash(brief.body) != brief.content_hash:
        raise OperationError("editor_brief_unavailable")
    checks = await preflight(s, post, revision)
    records: list[c.RecordView] = []
    if len(checks.checked_record_ids) > 100 or len(checks.findings) > 250:
        raise OperationError("editor_context_too_large")
    for record_id in checks.checked_record_ids:
        row = await record(s, wid, record_id, brand_id=brand)
        if not await current(s, row) or c.canonical_hash(row.body) != row.content_hash:
            raise OperationError("editor_evidence_unavailable")
        records.append(c.RecordView.model_validate(row))
    unavailable = {
        "evidence_unavailable",
        "brand_profile_required",
        "claim_policy_required",
        "media_unavailable",
        "media_changed",
    }
    if any(f.code in unavailable for f in checks.findings):
        raise OperationError("editor_context_unavailable")
    result = EditorContext(
        post_id=pid,
        brand_id=brand,
        revision=c.RevisionView.model_validate(revision),
        brief=c.RecordView.model_validate(brief),
        records=records,
        preflight_findings=checks.findings,
    )
    encoded = result.model_dump_json()
    if len(encoded.encode("utf-8")) > 100_000:
        raise OperationError("editor_context_too_large")
    safe_text(encoded)
    return result


def validate_review(review: EditorialReview, context: EditorContext) -> None:
    safe_text(review.model_dump_json())
    if (
        review.revision_id != context.revision.id
        or review.content_hash != context.revision.content_hash
        or review.context_hash != c.canonical_hash(context)
    ):
        raise OperationError("model_review_binding_invalid")
    ids = {r.id for r in context.records} | {context.brief.id}
    for finding in review.findings:
        if not set(finding.record_ids) <= ids:
            raise OperationError("model_review_evidence_invalid")
        if finding.location == "variant":
            index = finding.variant_index
            if index is None or index >= len(context.revision.body.variants):
                raise OperationError("model_review_location_invalid")
            if not finding.quote or finding.quote not in context.revision.body.variants[index].text:
                raise OperationError("model_review_quote_invalid")
        elif finding.variant_index is not None or finding.quote:
            raise OperationError("model_review_location_invalid")
        if finding.location == "evidence" and not finding.record_ids:
            raise OperationError("model_review_evidence_invalid")
    if review.recommendation == "pass" and (
        any(f.severity == "blocking" for f in review.findings)
        or any(f.severity == "blocker" for f in context.preflight_findings)
        or bool(context.revision.media_manifest)
    ):
        raise OperationError("model_review_conflicts_with_checks")
