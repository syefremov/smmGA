"""Deterministic pilot checks; no network, legal inference or automatic human approval."""

import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import content as d
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.content_models import ContentRecord, Post, PostRevision
from smm_gpt.infrastructure.models import FileMetadata, utcnow
from smm_gpt.services.content_records import current, record


async def media_manifest(
    s: AsyncSession, wid: UUID, body: d.RevisionBody
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for variant in body.variants:
        for attachment in variant.media:
            row = await s.scalar(
                select(FileMetadata).where(
                    FileMetadata.workspace_id == wid, FileMetadata.id == attachment.file_id
                )
            )
            if row is None:
                raise OperationError("media_unavailable", 422)
            result.append(
                {
                    "destination": variant.destination,
                    "file_id": str(row.id),
                    "sha256": row.sha256,
                    "content_type": row.content_type,
                    "size_bytes": row.size_bytes,
                    "alt": attachment.alt,
                    "rights_confirmed": attachment.rights_confirmed,
                }
            )
    return result


async def preflight(
    s: AsyncSession, post: Post, revision: PostRevision, horizon: datetime | None = None
) -> d.Preflight:
    horizon = horizon or utcnow()
    body = d.RevisionBody.model_validate(revision.body)
    findings: list[d.Finding] = []
    checked: set[UUID] = set()

    def issue(code: str, location: str, rid: UUID | None = None, *, warning: bool = False) -> None:
        findings.append(
            d.Finding(
                code=code,
                severity="warning" if warning else "blocker",
                location=location,
                record_id=rid,
            )
        )

    async def evidence(rid: UUID, kind: str, location: str) -> ContentRecord | None:
        try:
            row = await record(s, post.workspace_id, rid, kind, post.brand_id)
            checked.add(rid)
            if not await current(s, row, horizon):
                issue("unverified_or_stale_evidence", location, rid)
            return row
        except OperationError:
            issue("evidence_unavailable", location)
            return None

    for index, _gap in enumerate(body.knowledge_gaps):
        issue("knowledge_gap", f"knowledge_gaps.{index}")
    brief_row = await record(s, post.workspace_id, post.brief_id, "brief", post.brand_id)
    brief = d.Brief.model_validate(brief_row.body)
    if brief_row.expires_at <= horizon:
        issue("brief_expired", "brief", brief_row.id)
    if brief.product_id and not body.fact_ids:
        issue("product_facts_required", "facts")
    for fid in body.fact_ids:
        fact_row = await evidence(fid, "product_fact", "facts")
        if fact_row:
            fact = d.ProductFact.model_validate(fact_row.body)
            await evidence(fact.source_item_id, "source_item", "facts.source")
            product_row = await evidence(
                fact.product_version_id, "product_version", "facts.product"
            )
            if product_row:
                product = d.ProductVersion.model_validate(product_row.body)
                await evidence(product.source_item_id, "source_item", "product.source")
                if product.product_id != brief.product_id:
                    issue("fact_product_mismatch", "facts", fid)
    # Latest confirmed version per family. A draft never silently replaces an active policy.
    policies = (
        await s.scalars(
            select(ContentRecord)
            .where(
                ContentRecord.workspace_id == post.workspace_id,
                ContentRecord.brand_id == post.brand_id,
                ContentRecord.kind.in_(["brand_profile", "claim_policy"]),
                ContentRecord.confirmed_by.is_not(None),
            )
            .distinct(ContentRecord.family_id)
            .order_by(ContentRecord.family_id, ContentRecord.number.desc())
            .limit(51)
        )
    ).all()
    if len(policies) > 50:
        raise OperationError("policy_limit_exceeded", 422)
    for kind in ("brand_profile", "claim_policy"):
        if not any(row.kind == kind for row in policies):
            issue(f"{kind}_required", "brand")
    full_text = "\n".join(v.text for v in body.variants).casefold()
    for row in policies:
        await evidence(row.id, row.kind, "brand")
        profile = d.ARTIFACT.validate_python(row.body)
        if isinstance(profile, (d.BrandProfile, d.ClaimPolicy)):
            await evidence(profile.source_item_id, "source_item", "brand.source")
        if isinstance(profile, d.ClaimPolicy):
            for index, rule in enumerate(profile.rules):
                if rule.phrase.casefold() in full_text:
                    issue(
                        "claim_rule_match",
                        f"claim_policy.rules.{index}",
                        row.id,
                        warning=rule.severity == "warning",
                    )
            for phrase in profile.required_disclaimers:
                for index, variant in enumerate(body.variants):
                    if phrase.casefold() not in variant.text.casefold():
                        issue("disclaimer_missing", f"variants.{index}.text", row.id)
    for index, variant in enumerate(body.variants):
        # Conservative project limits, not a claim about current VK API specifications.
        if len(variant.text) > 4000 or len(variant.media) > 4:
            issue("pilot_format_limit", f"variants.{index}")
        if re.search(r"(?:javascript|data|file):|https?://[^\s]*@", variant.text, re.I):
            issue("unsafe_link", f"variants.{index}.text")
    try:
        actual_media = await media_manifest(s, post.workspace_id, body)
        if actual_media != revision.media_manifest:
            issue("media_changed", "media")
        for index, item in enumerate(actual_media):
            if item["content_type"] not in {"image/jpeg", "image/png", "image/webp", "video/mp4"}:
                issue("media_type_not_supported", f"media.{index}")
        if actual_media:
            issue("media_bytes_require_manual_check", "media", warning=True)
    except OperationError:
        issue("media_unavailable", "media")
    expected = d.canonical_hash({"body": revision.body, "media_manifest": revision.media_manifest})
    if expected != revision.content_hash:
        issue("revision_integrity_error", "revision")
    issue("human_claims_review_required", "review", warning=True)
    return d.Preflight(
        revision_id=revision.id,
        content_hash=revision.content_hash,
        checked_at=utcnow(),
        findings=findings,
        checked_record_ids=sorted(checked),
        passed=not any(f.severity == "blocker" for f in findings),
    )
