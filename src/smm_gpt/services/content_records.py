"""Typed immutable reference ledger. Sources are untrusted data by default."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import content as d
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.content_models import ContentLink, ContentRecord
from smm_gpt.infrastructure.models import Brand, Product, Source, utcnow


async def workspace_lock(s: AsyncSession, wid: UUID) -> None:
    # Coarse per-workspace serialization keeps dependency/policy/approval races deterministic.
    # It is bounded to a single transaction, and performs no network calls under the lock.
    await s.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": f"content:{wid}"},
    )


async def member(s: AsyncSession, wid: UUID, user_id: UUID) -> None:
    # Runtime RLS exposes only the actor's own membership; use a narrowly granted check.
    allowed = await s.scalar(
        text("SELECT smm_assignable_member(:wid, :uid)"), {"wid": wid, "uid": user_id}
    )
    if allowed is not True:
        raise OperationError("assignee_unavailable", 422)


async def record(
    s: AsyncSession,
    wid: UUID,
    record_id: UUID,
    kind: str | None = None,
    brand_id: UUID | None = None,
) -> ContentRecord:
    row = await s.scalar(
        select(ContentRecord).where(
            ContentRecord.workspace_id == wid, ContentRecord.id == record_id
        )
    )
    if row is None or (kind and row.kind != kind) or (brand_id and row.brand_id != brand_id):
        raise OperationError("record_unavailable", 422)
    return row


async def current(s: AsyncSession, row: ContentRecord, horizon: datetime | None = None) -> bool:
    if not row.confirmed_by or row.expires_at <= (horizon or utcnow()):
        return False
    newest = await s.scalar(
        select(func.max(ContentRecord.number)).where(
            ContentRecord.workspace_id == row.workspace_id,
            ContentRecord.family_id == row.family_id,
            ContentRecord.confirmed_by.is_not(None),
        )
    )
    return newest == row.number


async def validate_body(
    s: AsyncSession, wid: UUID, body: d.Artifact, *, confirming: bool = False
) -> list[ContentRecord]:
    if (
        await s.scalar(select(Brand.id).where(Brand.workspace_id == wid, Brand.id == body.brand_id))
        is None
    ):
        raise OperationError("brand_unavailable", 422)
    links: list[tuple[UUID, str]] = []
    if isinstance(body, d.SourceItem):
        if (
            await s.scalar(
                select(Source.id).where(Source.workspace_id == wid, Source.id == body.source_id)
            )
            is None
        ):
            raise OperationError("source_unavailable", 422)
        if body.observed_at > utcnow():
            raise OperationError("observation_in_future", 422)
        # Metadata only, never fetch arbitrary source URLs.
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(body.locator)
        except ValueError:
            raise OperationError("unsafe_source_locator", 422) from None
        if (
            parsed.scheme not in {"https", "owner-input"}
            or (parsed.scheme == "https" and not parsed.hostname)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise OperationError("unsafe_source_locator", 422)
    if isinstance(body, (d.BrandProfile, d.ProductVersion, d.ProductFact, d.ClaimPolicy)):
        links.append((body.source_item_id, "source_item"))
    if (
        isinstance(body, (d.ProductVersion, d.Brief))
        and body.product_id
        and (
            await s.scalar(
                select(Product.id).where(Product.workspace_id == wid, Product.id == body.product_id)
            )
            is None
        )
    ):
        raise OperationError("product_unavailable", 422)
    if isinstance(body, d.ProductFact):
        links.append((body.product_version_id, "product_version"))
    if isinstance(body, d.Research):
        if not body.source_item_ids:
            raise OperationError("research_sources_required", 422)
        links.extend((sid, "source_item") for sid in body.source_item_ids)
    if isinstance(body, d.Campaign):
        await member(s, wid, body.owner_id)
    if isinstance(body, d.ContentPlan):
        links.append((body.campaign_id, "campaign"))
    if isinstance(body, d.Brief):
        if body.campaign_id:
            links.append((body.campaign_id, "campaign"))
        if body.research_id:
            links.append((body.research_id, "research"))
    if isinstance(body, d.Idea):
        links.append((body.brief_id, "brief"))
    refs = [await record(s, wid, rid, kind, body.brand_id) for rid, kind in links]
    if confirming:
        for ref in refs:
            if ref.kind in {"source_item", "product_version"} and not await current(s, ref):
                raise OperationError("unverified_or_stale_source", 422)
        if isinstance(body, (d.ProductFact, d.ClaimPolicy)):
            source = d.ARTIFACT.validate_python(refs[0].body)
            if isinstance(source, d.SourceItem) and source.evidence_kind == "hypothesis":
                raise OperationError("hypothesis_is_not_evidence", 422)
    if isinstance(body, d.ContentPlan):
        campaign = d.Campaign.model_validate(refs[0].body)
        if any(
            not campaign.starts_at <= slot.planned_at <= campaign.ends_at for slot in body.slots
        ):
            raise OperationError("slot_outside_campaign", 422)
    return refs


async def create_record(
    s: AsyncSession,
    wid: UUID,
    actor_id: UUID,
    command: d.CreateRecord,
    confirmed_by: UUID | None = None,
) -> ContentRecord:
    if command.expires_at <= utcnow():
        raise OperationError("record_already_expired", 422)
    refs = await validate_body(s, wid, command.body)
    rid = uuid4()
    family, number = rid, 1
    if not command.replaces_id and isinstance(
        command.body, (d.BrandProfile, d.ClaimPolicy, d.ProductVersion)
    ):
        query = select(ContentRecord.id).where(
            ContentRecord.workspace_id == wid,
            ContentRecord.brand_id == command.body.brand_id,
            ContentRecord.kind == command.body.kind,
        )
        if isinstance(command.body, d.ProductVersion):
            query = query.where(ContentRecord.product_id == command.body.product_id)
        if await s.scalar(query.limit(1)):
            raise OperationError("record_family_exists")
    if command.replaces_id:
        previous = await record(
            s, wid, command.replaces_id, command.body.kind, command.body.brand_id
        )
        if (
            isinstance(command.body, d.ProductVersion)
            and previous.product_id != command.body.product_id
        ):
            raise OperationError("product_family_mismatch", 422)
        last = await s.scalar(
            select(func.max(ContentRecord.number)).where(
                ContentRecord.workspace_id == wid, ContentRecord.family_id == previous.family_id
            )
        )
        if previous.number != last:
            raise OperationError("version_conflict")
        family, number = previous.family_id, previous.number + 1
    row = ContentRecord(
        id=rid,
        workspace_id=wid,
        brand_id=command.body.brand_id,
        source_id=command.body.source_id if isinstance(command.body, d.SourceItem) else None,
        product_id=command.body.product_id
        if isinstance(command.body, (d.ProductVersion, d.Brief))
        else None,
        family_id=family,
        number=number,
        kind=command.body.kind,
        body=command.body.model_dump(mode="json"),
        content_hash=d.canonical_hash(command.body),
        actor_id=actor_id,
        confirmed_by=confirmed_by,
        expires_at=command.expires_at,
    )
    s.add(row)
    await s.flush()
    for ref in {r.id: r for r in refs}.values():
        s.add(ContentLink(workspace_id=wid, record_id=row.id, target_id=ref.id))
    return row


async def confirm_record(
    s: AsyncSession, wid: UUID, actor_id: UUID, command: d.ConfirmRecord
) -> ContentRecord:
    original = await record(s, wid, command.record_id)
    if original.content_hash != command.content_hash or original.confirmed_by:
        raise OperationError("record_confirmation_conflict")
    body = d.ARTIFACT.validate_python(original.body)
    await validate_body(s, wid, body, confirming=True)
    row = await create_record(
        s,
        wid,
        actor_id,
        d.CreateRecord(
            body=body,
            replaces_id=original.id,
            expires_at=original.expires_at,
            idempotency_key=command.idempotency_key,
        ),
        confirmed_by=actor_id,
    )
    return row
