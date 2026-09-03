"""Durable bounded text queue. No model, approval, original-file or content mutation rights."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text

from smm_gpt.core.config import Settings, get_settings
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.knowledge_models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndex,
    KnowledgeVersion,
)
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import audit, digest
from smm_gpt.services.ingestion_state import allowed, finish, reconcile
from smm_gpt.services.knowledge_text import chunks, normalize


async def process(database: Database, wid: UUID, iid: UUID, actor: UUID) -> bool:
    """Claim/fence/commit. Lost workers cannot finalize a newer attempt."""
    lease = uuid4()
    async with database.transaction(actor, wid) as s:
        index = await s.scalar(
            select(KnowledgeIndex)
            .where(
                KnowledgeIndex.workspace_id == wid,
                KnowledgeIndex.id == iid,
                KnowledgeIndex.actor_id == actor,
            )
            .with_for_update(skip_locked=True)
        )
        if index is None or index.state != "queued":
            return False
        if index.lease_until and index.lease_until > utcnow():
            return False
        doc = await s.get(KnowledgeDocument, index.document_id)
        if not await allowed(s, wid, actor, index.identity_id):
            return False  # Privileged reconciler can close jobs hidden by revoked RLS.
        if doc is None or doc.archived:
            finish(index, "failed", "authorization_or_document_changed")
            audit(s, actor, wid, uuid4(), "knowledge.index_blocked", "failed", iid)
            return False
        if index.created_at <= utcnow() - timedelta(hours=24):
            finish(index, "failed", "queue_expired")
            audit(s, actor, wid, uuid4(), "knowledge.index_blocked", "failed", iid)
            return False
        if index.attempts >= 3:
            finish(index, "failed", "attempts_exhausted")
            audit(s, actor, wid, uuid4(), "knowledge.index_blocked", "failed", iid)
            return False
        version = await s.get(KnowledgeVersion, index.document_version_id)
        if version is None:
            raise OperationError("source_unavailable")
        if version.effective_to <= utcnow():
            finish(index, "failed", "source_expired")
            audit(s, actor, wid, uuid4(), "knowledge.index_blocked", "failed", iid)
            return False
        original, format, expected_hash = version.original, version.format, version.content_hash
        index.state, index.lease_id = "processing", lease
        index.lease_until, index.attempts = utcnow() + timedelta(seconds=120), index.attempts + 1
        index.version += 1
        index.started_at, index.finished_at = utcnow(), None
        audit(s, actor, wid, uuid4(), "knowledge.index_started", "processing", iid)
    error: str | None = None
    prepared: list[tuple[str, str]] = []
    try:
        if digest(original) != expected_hash:
            raise OperationError("original_hash_mismatch")
        prepared = await asyncio.wait_for(
            asyncio.to_thread(lambda: chunks(normalize(original, format))), timeout=30
        )
    except OperationError as exc:
        error = exc.code
    except Exception:
        # Never persist parser messages or submitted text, even on a malformed input.
        error = "text_processing_failed"
    async with database.transaction(actor, wid) as s:
        index = await s.scalar(
            select(KnowledgeIndex)
            .where(KnowledgeIndex.workspace_id == wid, KnowledgeIndex.id == iid)
            .with_for_update()
        )
        if index is None or index.lease_id != lease or index.state != "processing":
            return False
        doc = await s.get(KnowledgeDocument, index.document_id)
        if not await allowed(s, wid, actor, index.identity_id):
            return False
        if doc is None or doc.archived:
            error = "authorization_or_document_changed"
        if index.lease_until is None or index.lease_until <= utcnow():
            return False
        current = await s.get(KnowledgeVersion, index.document_version_id)
        if current is None or current.effective_to <= utcnow():
            error = "source_expired"
        if error is None:
            s.add_all(
                [
                    KnowledgeChunk(
                        workspace_id=wid,
                        document_id=index.document_id,
                        index_id=iid,
                        ordinal=ordinal,
                        section=section,
                        body=body,
                        search_text=(section + " " + body).casefold(),
                        content_hash=digest(body),
                    )
                    for ordinal, (section, body) in enumerate(prepared)
                ]
            )
            await s.flush()
            finish(index, "ready", None)
        else:
            finish(index, "failed", error)
        audit(s, actor, wid, uuid4(), "knowledge.index", index.state, iid)
    return error is None


async def poll(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not settings.knowledge_worker_enabled:
        return 0
    database = Database(settings.database_url.get_secret_value(), 5)
    try:
        await database.require_restricted_role()
        await reconcile(database, "index")
        async with database.transaction() as s:
            rows = (await s.execute(text("SELECT * FROM public.smm_knowledge_pending()"))).all()
        completed = 0
        for row in rows:
            completed += await process(database, row.workspace_id, row.index_id, row.actor_id)
        return completed
    finally:
        await database.close()
