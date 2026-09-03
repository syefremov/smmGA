"""Upload, private preview and import eligibility; never activate knowledge automatically."""

import asyncio
import base64
import binascii
import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.core.config import Settings
from smm_gpt.domain import knowledge_files as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.operations import OperationError, Page
from smm_gpt.infrastructure.file_models import FileRetryReceipt, KnowledgeExtraction, KnowledgeFile
from smm_gpt.infrastructure.file_storage import FileStore, VolumeFileStore
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.ingestion_state import allowed
from smm_gpt.services.knowledge import brand_exists, lock
from smm_gpt.services.knowledge_text import safe_text

RETRYABLE = frozenset(
    {
        "scanner_unavailable",
        "scanner_signatures_stale",
        "sandbox_unavailable",
        "parser_timeout",
        "parser_resource_limit",
        "processing_interrupted",
    }
)


async def file_row(s: AsyncSession, wid: UUID, fid: UUID) -> KnowledgeFile:
    row = await s.scalar(
        select(KnowledgeFile).where(KnowledgeFile.workspace_id == wid, KnowledgeFile.id == fid)
    )
    if row is None:
        raise OperationError("not_found", 404)
    return row


async def importable(s: AsyncSession, wid: UUID, fid: UUID, bid: UUID) -> KnowledgeExtraction:
    row = await file_row(s, wid, fid)
    extracted = await s.scalar(
        select(KnowledgeExtraction).where(
            KnowledgeExtraction.workspace_id == wid, KnowledgeExtraction.file_id == fid
        )
    )
    if row.brand_id != bid or row.state != "ready" or extracted is None:
        raise OperationError("file_not_ready")
    if extracted.signatures_updated_at < utcnow() - timedelta(hours=48):
        raise OperationError("file_scan_expired")
    if digest(extracted.text) != extracted.text_hash:
        raise OperationError("extraction_hash_mismatch")
    return extracted


class KnowledgeFileService:
    def __init__(self, access: AccessService, settings: Settings, store: FileStore | None = None):
        self.access, self.settings = access, settings
        self.store = store or VolumeFileStore(settings.media_root)

    async def submit(
        self, actor: Principal, wid: UUID, c: d.SubmitFile, request: UUID
    ) -> d.FileReceipt:
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            if not self.settings.knowledge_files_enabled:
                raise OperationError("binary_ingestion_disabled", 503)
            await lock(s, wid)
            await brand_exists(s, wid, c.brand_id)
            safe_text(c.filename)
            try:
                data = base64.b64decode(c.content_base64, validate=True)
            except (ValueError, binascii.Error):
                raise OperationError("invalid_file_encoding", 422) from None
            if not 0 < len(data) <= d.MAX_FILE_BYTES:
                raise OperationError("file_size_invalid", 422)
            if hashlib.sha256(data).hexdigest() != c.content_hash:
                raise OperationError("original_hash_mismatch", 422)
            if not c.filename.casefold().endswith("." + c.format) or not data.startswith(
                b"%PDF-" if c.format == "pdf" else b"PK\x03\x04"
            ):
                raise OperationError("file_type_mismatch", 422)
            fingerprint = canonical_hash(
                c.model_dump(mode="json", exclude={"idempotency_key", "content_base64"})
            )
            prior = await s.scalar(
                select(KnowledgeFile).where(
                    KnowledgeFile.workspace_id == wid,
                    KnowledgeFile.actor_id == actor.user_id,
                    KnowledgeFile.key_hash == digest(c.idempotency_key),
                )
            )
            if prior:
                if prior.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.FileReceipt(file_id=prior.id)
            # Per-person/workspace lifetime cap; no hidden cross-actor metadata aggregates.
            used, count = (
                await s.execute(
                    select(func.coalesce(func.sum(KnowledgeFile.byte_size), 0), func.count()).where(
                        KnowledgeFile.workspace_id == wid, KnowledgeFile.actor_id == actor.user_id
                    )
                )
            ).one()
            if used + len(data) > 100 * 1024 * 1024 or count >= 200:
                raise OperationError("file_storage_quota_exceeded", 429)
            row = KnowledgeFile(
                id=uuid4(),
                workspace_id=wid,
                actor_id=actor.user_id,
                identity_id=actor.identity_id,
                brand_id=c.brand_id,
                key_hash=digest(c.idempotency_key),
                request_hash=fingerprint,
                filename=c.filename,
                format=c.format,
                byte_size=len(data),
                content_hash=c.content_hash,
            )
            # Write once BEFORE DB commit. A failed transaction leaves an unaddressable orphan,
            # never a DB record pointing at a partially written file or an overwritten original.
            await asyncio.to_thread(self.store.put, row.id, data)
            s.add(row)
            audit(s, actor.user_id, wid, request, "knowledge.file_submitted", "queued", row.id)
            return d.FileReceipt(file_id=row.id)

    async def files(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.FileView]:
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            query = select(KnowledgeFile).where(KnowledgeFile.workspace_id == wid)
            if cursor:
                query = query.where(KnowledgeFile.id > cursor)
            rows = list((await s.scalars(query.order_by(KnowledgeFile.id).limit(limit + 1))).all())
            return Page(
                items=[d.FileView.model_validate(r) for r in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    async def read(self, actor: Principal, wid: UUID, fid: UUID, request: UUID) -> d.FileDetail:
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            row = await file_row(s, wid, fid)
            extracted = await s.scalar(
                select(KnowledgeExtraction).where(
                    KnowledgeExtraction.workspace_id == wid, KnowledgeExtraction.file_id == fid
                )
            )
            return d.FileDetail(
                **d.FileView.model_validate(row).model_dump(),
                extraction=d.ExtractionView.model_validate(extracted)
                if row.state == "ready" and extracted
                else None,
            )

    async def download(
        self, actor: Principal, wid: UUID, fid: UUID, request: UUID
    ) -> tuple[bytes, str]:
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            row = await file_row(s, wid, fid)
            await importable(s, wid, fid, row.brand_id)
            data = await asyncio.to_thread(self.store.get, row.id, row.content_hash)
            audit(s, actor.user_id, wid, request, "knowledge.original_download", "allowed", row.id)
            return data, row.format

    async def retry(
        self, actor: Principal, wid: UUID, c: d.RetryFile, request: UUID
    ) -> d.FileReceipt:
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            if not self.settings.knowledge_files_enabled:
                raise OperationError("binary_ingestion_disabled", 503)
            await lock(s, wid)
            fingerprint = canonical_hash(c.model_dump(mode="json", exclude={"idempotency_key"}))
            receipt = await s.scalar(
                select(FileRetryReceipt).where(
                    FileRetryReceipt.workspace_id == wid,
                    FileRetryReceipt.actor_id == actor.user_id,
                    FileRetryReceipt.key_hash == digest(c.idempotency_key),
                )
            )
            if receipt:
                if receipt.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.FileReceipt.model_validate(receipt.result)
            row = await file_row(s, wid, c.file_id)
            # Same row/actor/identity; Owner must use rescan to create a new authorized job.
            if not await allowed(s, wid, row.actor_id, row.identity_id):
                raise OperationError("file_retry_authorization_changed")
            if (
                row.state != "failed"
                or row.attempts != c.expected_attempts
                or row.attempts >= 3
                or row.error_code not in RETRYABLE
                or row.created_at <= utcnow() - timedelta(hours=24)
            ):
                raise OperationError("file_retry_not_allowed")
            row.state, row.error_code = "queued", None
            row.version += 1
            row.finished_at, row.lease_until = None, None
            result = d.FileReceipt(file_id=row.id)
            s.add(
                FileRetryReceipt(
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    file_id=row.id,
                    key_hash=digest(c.idempotency_key),
                    request_hash=fingerprint,
                    result=result.model_dump(mode="json"),
                )
            )
            audit(s, actor.user_id, wid, request, "knowledge.file_retried", "queued", row.id)
            return result

    async def rescan(
        self, actor: Principal, wid: UUID, c: d.RescanFile, request: UUID
    ) -> d.FileReceipt:
        # New immutable upload + job, never overwrite a previous scan/extraction decision.
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            row = await file_row(s, wid, c.file_id)
            data = await asyncio.to_thread(self.store.get, row.id, row.content_hash)
            command = d.SubmitFile.model_validate(
                dict(
                    idempotency_key=c.idempotency_key,
                    brand_id=row.brand_id,
                    filename=row.filename,
                    format=row.format,
                    content_hash=row.content_hash,
                    content_base64=base64.b64encode(data).decode("ascii"),
                )
            )
        return await self.submit(actor, wid, command, request)
