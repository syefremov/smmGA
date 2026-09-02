"""Scan then sandbox. No activation, content commands or human service principal."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text

from smm_gpt.core.config import Settings, get_settings
from smm_gpt.domain.knowledge_files import FileFormat
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.file_models import KnowledgeExtraction, KnowledgeFile
from smm_gpt.infrastructure.file_storage import FileStore, VolumeFileStore
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import audit, digest
from smm_gpt.services.file_parser import ParsedFile, Parser, SandboxedParser
from smm_gpt.services.file_scanner import ClamScanner, ScanEvidence, Scanner
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.workers.knowledge import allowed


async def process(
    database: Database,
    store: FileStore,
    scanner: Scanner,
    parser: Parser,
    wid: UUID,
    fid: UUID,
    actor: UUID,
) -> bool:
    token = uuid4()
    async with database.transaction(actor, wid) as s:
        row = await s.scalar(
            select(KnowledgeFile)
            .where(
                KnowledgeFile.workspace_id == wid,
                KnowledgeFile.id == fid,
                KnowledgeFile.actor_id == actor,
            )
            .with_for_update(skip_locked=True)
        )
        if (
            row is None
            or row.state not in {"queued", "processing"}
            or (row.lease_until and row.lease_until > utcnow())
        ):
            return False
        if not await allowed(s, wid, actor, row.identity_id):
            row.state, row.error_code = "failed", "authorization_changed"
            return False
        if row.attempts >= 3:
            row.state, row.error_code = "failed", "attempts_exhausted"
            return False
        if row.format not in ("pdf", "docx"):
            row.state, row.error_code = "failed", "file_type_mismatch"
            return False
        format: FileFormat = "pdf" if row.format == "pdf" else "docx"
        expected_hash = row.content_hash
        row.state, row.lease_id = "processing", token
        row.attempts += 1
        row.lease_until = utcnow() + timedelta(seconds=120)
    evidence: ScanEvidence | None = None
    parsed: ParsedFile | None = None
    error: str | None = None
    try:
        data = await asyncio.to_thread(store.get, fid, expected_hash)
        evidence = await scanner.scan(data)
        if (
            not utcnow() - timedelta(hours=48)
            <= evidence.signatures_updated_at
            <= utcnow() + timedelta(minutes=10)
        ):
            raise OperationError("scanner_signatures_stale")
        parsed = await parser.parse(data, format)
        safe_text(parsed.text)
    except OperationError as exc:
        error = exc.code
    except Exception:
        error = "file_processing_failed"
    async with database.transaction(actor, wid) as s:
        row = await s.scalar(
            select(KnowledgeFile)
            .where(KnowledgeFile.workspace_id == wid, KnowledgeFile.id == fid)
            .with_for_update()
        )
        if (
            row is None
            or row.state != "processing"
            or row.lease_id != token
            or not row.lease_until
            or row.lease_until <= utcnow()
        ):
            return False
        if not await allowed(s, wid, actor, row.identity_id):
            error = "authorization_changed"
        if not error and evidence and parsed:
            s.add(
                KnowledgeExtraction(
                    workspace_id=wid,
                    file_id=fid,
                    text=parsed.text,
                    text_hash=digest(parsed.text),
                    parser_version=parsed.parser_version,
                    scan_engine=evidence.engine,
                    signature_version=evidence.signature_version,
                    signatures_updated_at=evidence.signatures_updated_at,
                    scanned_at=evidence.scanned_at,
                )
            )
            await s.flush()
            row.state = "ready"
        else:
            row.state = "failed"
        row.error_code, row.lease_until = error, None
        audit(s, actor, wid, uuid4(), "knowledge.file_processed", row.state, fid)
    return error is None


async def poll(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not settings.knowledge_files_enabled:
        return 0
    database = Database(settings.database_url.get_secret_value(), 5)
    try:
        await database.require_restricted_role()
        async with database.transaction() as s:
            pending = (await s.execute(text("SELECT * FROM public.smm_files_pending()"))).all()
        count = 0
        for row in pending:
            count += await process(
                database,
                VolumeFileStore(settings.media_root),
                ClamScanner(settings.clamav_host, settings.clamav_port),
                SandboxedParser(),
                row.workspace_id,
                row.file_id,
                row.actor_id,
            )
        return count
    finally:
        await database.close()
