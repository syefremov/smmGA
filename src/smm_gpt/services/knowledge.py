"""One knowledge workflow for REST and MCP. Worker never activates an index."""

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import knowledge as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.operations import OperationError, Page
from smm_gpt.infrastructure.knowledge_models import (
    KnowledgeActivation,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndex,
    KnowledgeNote,
    KnowledgeNoteReview,
    KnowledgeReceipt,
    KnowledgeVersion,
    RetrievalRun,
)
from smm_gpt.infrastructure.models import Brand, utcnow
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.knowledge_text import safe_text


async def lock(s: AsyncSession, wid: UUID) -> None:
    await s.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int(digest(f"knowledge:{wid}")[:15], 16)},
    )


async def document(s: AsyncSession, wid: UUID, did: UUID) -> KnowledgeDocument:
    row = await s.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.workspace_id == wid, KnowledgeDocument.id == did
        )
    )
    if row is None:
        raise OperationError("not_found", 404)
    return row


async def brand_exists(s: AsyncSession, wid: UUID, bid: UUID) -> None:
    if not await s.scalar(select(Brand.id).where(Brand.workspace_id == wid, Brand.id == bid)):
        raise OperationError("not_found", 404)


def query_vector(query: str) -> object:
    # Explicit config is mandatory: application/server locale does not change retrieval.
    return func.websearch_to_tsquery("russian", query.casefold()).op("||")(
        func.websearch_to_tsquery("simple", query.casefold())
    )


def citation(c: KnowledgeChunk, doc: KnowledgeDocument, v: KnowledgeVersion) -> d.Citation:
    return d.Citation(
        chunk_id=c.id,
        document_id=doc.id,
        document_version_id=v.id,
        index_id=c.index_id,
        content_hash=c.content_hash,
        title=doc.title,
        section=c.section,
        text=c.body,
        source_uri=v.source_uri,
        source_date=v.source_date,
        effective_to=v.effective_to,
    )


async def eligible_citation(s: AsyncSession, wid: UUID, cid: UUID, bid: UUID) -> d.Citation:
    row = (
        await s.execute(
            select(KnowledgeChunk, KnowledgeDocument, KnowledgeVersion)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeIndex, KnowledgeIndex.id == KnowledgeChunk.index_id)
            .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeIndex.document_version_id)
            .where(
                KnowledgeChunk.workspace_id == wid,
                KnowledgeChunk.id == cid,
                KnowledgeDocument.brand_id == bid,
                KnowledgeDocument.archived.is_(False),
                KnowledgeDocument.active_index_id == KnowledgeIndex.id,
                KnowledgeIndex.state == "ready",
                KnowledgeVersion.effective_from <= utcnow(),
                KnowledgeVersion.effective_to > utcnow(),
            )
        )
    ).first()
    if row is None:
        raise OperationError("source_unavailable", 409)
    return citation(row[0], row[1], row[2])


async def retrieve(
    s: AsyncSession,
    wid: UUID,
    query: d.SearchRequest,
    *,
    at: datetime,
    workspace_only: bool = False,
) -> list[d.Citation]:
    """Production search and benchmarks share the exact ranked SQL path.

    workspace_only can only NARROW existing RLS visibility; it never impersonates a user.
    """
    vector = query_vector(query.query)
    statement = (
        select(KnowledgeChunk, KnowledgeDocument, KnowledgeVersion)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .join(KnowledgeIndex, KnowledgeIndex.id == KnowledgeChunk.index_id)
        .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeIndex.document_version_id)
        .where(
            KnowledgeChunk.workspace_id == wid,
            KnowledgeDocument.brand_id == query.brand_id,
            KnowledgeDocument.archived.is_(False),
            KnowledgeIndex.state == "ready",
            KnowledgeDocument.active_index_id == KnowledgeIndex.id,
            KnowledgeVersion.effective_from <= at,
            KnowledgeVersion.effective_to > at,
            KnowledgeChunk.search_vector.op("@@")(vector),
        )
        .order_by(func.ts_rank_cd(KnowledgeChunk.search_vector, vector).desc(), KnowledgeChunk.id)
        .limit(query.limit)
    )
    if workspace_only:
        statement = statement.where(KnowledgeDocument.visibility == "workspace")
    rows = (await s.execute(statement)).all()
    return [citation(row[0], row[1], row[2]) for row in rows]


class KnowledgeService:
    def __init__(self, access: AccessService):
        self.access = access

    async def execute(
        self, actor: Principal, wid: UUID, command: d.KnowledgeCommand, request: UUID
    ) -> d.KnowledgeResult:
        permission = (
            Permission.APPROVE
            if isinstance(
                command, (d.ActivateIndex, d.ArchiveDocument, d.ReviewNote, d.ProposeNote)
            )
            else Permission.KNOWLEDGE
        )
        if isinstance(command, d.SubmitDocument) and command.visibility == "owner":
            permission = Permission.APPROVE
        async with self.access.authorized(actor, wid, permission, request) as s:
            await lock(s, wid)
            fingerprint = canonical_hash(
                command.model_dump(mode="json", exclude={"idempotency_key"})
            )
            previous = await s.scalar(
                select(KnowledgeReceipt).where(
                    KnowledgeReceipt.workspace_id == wid,
                    KnowledgeReceipt.actor_id == actor.user_id,
                    KnowledgeReceipt.key_hash == digest(command.idempotency_key),
                )
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.KnowledgeResult.model_validate(previous.result)
            result = await self._execute(s, actor, wid, command)
            s.add(
                KnowledgeReceipt(
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    key_hash=digest(command.idempotency_key),
                    request_hash=fingerprint,
                    result=result.model_dump(mode="json"),
                )
            )
            audit(
                s,
                actor.user_id,
                wid,
                request,
                "knowledge." + command.action,
                "succeeded",
                result.entity_id,
            )
            return result

    async def _execute(
        self, s: AsyncSession, actor: Principal, wid: UUID, c: d.KnowledgeCommand
    ) -> d.KnowledgeResult:
        note: KnowledgeNote | None
        if isinstance(c, d.SubmitDocument):
            safe_text(c.text)
            safe_text(c.title)
            safe_text(c.source_uri)
            await brand_exists(s, wid, c.brand_id)
            if c.document_id:
                doc = await document(s, wid, c.document_id)
                if (
                    doc.version != c.expected_version
                    or doc.archived
                    or (doc.brand_id, doc.title, doc.document_type, doc.visibility)
                    != (c.brand_id, c.title, c.document_type, c.visibility)
                ):
                    raise OperationError("document_conflict")
            else:
                if c.expected_version != 0:
                    raise OperationError("document_conflict")
                doc = KnowledgeDocument(
                    id=uuid4(),
                    workspace_id=wid,
                    brand_id=c.brand_id,
                    title=c.title,
                    document_type=c.document_type,
                    visibility=c.visibility,
                    actor_id=actor.user_id,
                    version=0,
                    archived=False,
                )
                s.add(doc)
                await s.flush()
            fingerprint = canonical_hash(
                c.model_dump(
                    mode="json",
                    exclude={"action", "idempotency_key", "document_id", "expected_version"},
                )
            )
            version = await s.scalar(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.workspace_id == wid,
                    KnowledgeVersion.document_id == doc.id,
                    KnowledgeVersion.fingerprint == fingerprint,
                )
            )
            if version:
                existing = await s.scalar(
                    select(KnowledgeIndex)
                    .where(
                        KnowledgeIndex.workspace_id == wid,
                        KnowledgeIndex.document_version_id == version.id,
                    )
                    .order_by(KnowledgeIndex.created_at.desc(), KnowledgeIndex.id)
                    .limit(1)
                )
                return d.KnowledgeResult(
                    entity_id=doc.id,
                    version=doc.version,
                    index_id=existing.id if existing else None,
                )
            version = KnowledgeVersion(
                id=uuid4(),
                workspace_id=wid,
                document_id=doc.id,
                actor_id=actor.user_id,
                original=c.text,
                format=c.format,
                fingerprint=fingerprint,
                content_hash=digest(c.text),
                source_uri=c.source_uri,
                source_date=c.source_date,
                effective_from=c.effective_from,
                effective_to=c.effective_to,
            )
            s.add(version)
            await s.flush()
            return self._queue(s, actor, doc, version)
        if isinstance(c, d.ProposeNote):
            await brand_exists(s, wid, c.brand_id)
            for value in (c.text, c.purpose, c.safe_alternative):
                safe_text(value)
            if c.effective_to <= utcnow() or (c.kind == "memory" and not c.evidence_ids):
                raise OperationError("evidence_and_future_expiry_required", 422)
            for cid in c.evidence_ids:
                await eligible_citation(s, wid, cid, c.brand_id)
            note = KnowledgeNote(
                id=uuid4(),
                workspace_id=wid,
                actor_id=actor.user_id,
                brand_id=c.brand_id,
                kind=c.kind,
                text=c.text,
                purpose=c.purpose,
                safe_alternative=c.safe_alternative,
                evidence_ids=[str(x) for x in c.evidence_ids],
                effective_to=c.effective_to,
            )
            s.add(note)
            return d.KnowledgeResult(entity_id=note.id, version=1)
        if isinstance(c, d.ReviewNote):
            note = await s.scalar(
                select(KnowledgeNote).where(
                    KnowledgeNote.workspace_id == wid, KnowledgeNote.id == c.note_id
                )
            )
            if note is None:
                raise OperationError("not_found", 404)
            prior = await s.scalar(
                select(KnowledgeNoteReview.id).where(
                    KnowledgeNoteReview.workspace_id == wid, KnowledgeNoteReview.note_id == note.id
                )
            )
            if prior or note.effective_to <= utcnow():
                raise OperationError("note_already_reviewed_or_expired")
            if (c.decision == "resolve" and note.kind != "gap") or (
                c.decision == "accept_for_curation" and note.kind != "memory"
            ):
                raise OperationError("invalid_note_decision", 422)
            safe_text(c.reason)
            for cid in c.evidence_ids:
                await eligible_citation(s, wid, cid, note.brand_id)
            s.add(
                KnowledgeNoteReview(
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    note_id=note.id,
                    decision=c.decision,
                    reason=c.reason,
                    evidence_ids=[str(x) for x in c.evidence_ids],
                )
            )
            return d.KnowledgeResult(entity_id=note.id, version=2)
        doc = await document(s, wid, c.document_id)
        if doc.version != c.expected_version or doc.archived:
            raise OperationError("document_conflict")
        if isinstance(c, d.ReindexDocument):
            version = await s.scalar(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.workspace_id == wid,
                    KnowledgeVersion.document_id == doc.id,
                    KnowledgeVersion.id == c.document_version_id,
                )
            )
            if version is None:
                raise OperationError("not_found", 404)
            return self._queue(s, actor, doc, version)
        if isinstance(c, d.ArchiveDocument):
            doc.archived = True
        else:
            index = await s.scalar(
                select(KnowledgeIndex).where(
                    KnowledgeIndex.workspace_id == wid,
                    KnowledgeIndex.document_id == doc.id,
                    KnowledgeIndex.id == c.index_id,
                )
            )
            if index is None or index.state != "ready" or index.content_hash != c.content_hash:
                raise OperationError("index_not_ready_or_changed")
            version = await s.get(KnowledgeVersion, index.document_version_id)
            if version is None or not version.effective_from <= utcnow() < version.effective_to:
                raise OperationError("source_not_current")
            for query in c.expected_queries:
                safe_text(query)
                found = await s.scalar(
                    select(KnowledgeChunk.id)
                    .where(
                        KnowledgeChunk.workspace_id == wid,
                        KnowledgeChunk.index_id == index.id,
                        KnowledgeChunk.search_vector.op("@@")(query_vector(query)),
                    )
                    .limit(1)
                )
                if not found:
                    raise OperationError("index_acceptance_failed")
            doc.active_index_id = index.id
            s.add(
                KnowledgeActivation(
                    workspace_id=wid,
                    index_id=index.id,
                    actor_id=actor.user_id,
                    content_hash=c.content_hash,
                    query_hashes=[digest(q) for q in c.expected_queries],
                )
            )
        doc.version += 1
        return d.KnowledgeResult(
            entity_id=doc.id, version=doc.version, index_id=doc.active_index_id
        )

    @staticmethod
    def _queue(
        s: AsyncSession, actor: Principal, doc: KnowledgeDocument, version: KnowledgeVersion
    ) -> d.KnowledgeResult:
        index = KnowledgeIndex(
            id=uuid4(),
            workspace_id=doc.workspace_id,
            document_id=doc.id,
            document_version_id=version.id,
            actor_id=actor.user_id,
            identity_id=actor.identity_id,
            content_hash=version.content_hash,
        )
        s.add(index)
        doc.version += 1
        return d.KnowledgeResult(entity_id=doc.id, version=doc.version, index_id=index.id)

    async def documents(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.DocumentView]:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            query = select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == wid)
            if cursor:
                query = query.where(KnowledgeDocument.id > cursor)
            rows = list(
                (await s.scalars(query.order_by(KnowledgeDocument.id).limit(limit + 1))).all()
            )
            return Page(
                items=[d.DocumentView.model_validate(r) for r in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    async def read_document(
        self, actor: Principal, wid: UUID, did: UUID, request: UUID
    ) -> d.DocumentDetail:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            doc = await document(s, wid, did)
            rows = list(
                (
                    await s.scalars(
                        select(KnowledgeIndex)
                        .where(
                            KnowledgeIndex.workspace_id == wid, KnowledgeIndex.document_id == did
                        )
                        .order_by(KnowledgeIndex.created_at.desc(), KnowledgeIndex.id)
                        .limit(21)
                    )
                ).all()
            )
            return d.DocumentDetail(
                **d.DocumentView.model_validate(doc).model_dump(),
                indexes=[d.IndexView.model_validate(r) for r in rows[:20]],
                indexes_truncated=len(rows) > 20,
            )

    async def preview(
        self,
        actor: Principal,
        wid: UUID,
        did: UUID,
        iid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.Citation]:
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            doc = await document(s, wid, did)
            index = await s.scalar(
                select(KnowledgeIndex).where(
                    KnowledgeIndex.workspace_id == wid,
                    KnowledgeIndex.document_id == did,
                    KnowledgeIndex.id == iid,
                )
            )
            if index is None:
                raise OperationError("not_found", 404)
            version = await s.get(KnowledgeVersion, index.document_version_id)
            if version is None:
                raise OperationError("not_found", 404)
            query = select(KnowledgeChunk).where(
                KnowledgeChunk.workspace_id == wid, KnowledgeChunk.index_id == iid
            )
            if cursor:
                query = query.where(KnowledgeChunk.id > cursor)
            rows = list((await s.scalars(query.order_by(KnowledgeChunk.id).limit(limit + 1))).all())
            values = [
                citation(c, doc, version).model_copy(update={"authority": "unreviewed_reference"})
                for c in rows[:limit]
            ]
            return Page(items=values, next_cursor=rows[limit - 1].id if len(rows) > limit else None)

    async def search(
        self, actor: Principal, wid: UUID, query: d.SearchRequest, request: UUID
    ) -> d.SearchResult:
        safe_text(query.query)
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            await brand_exists(s, wid, query.brand_id)
            citations = await retrieve(s, wid, query, at=utcnow())
            run = RetrievalRun(
                id=uuid4(),
                workspace_id=wid,
                actor_id=actor.user_id,
                brand_id=query.brand_id,
                query_hash=digest(query.query),
                algorithm="ru-simple-v1",
                chunk_ids=[str(c.chunk_id) for c in citations],
            )
            s.add(run)
            return d.SearchResult(run_id=run.id, citations=citations)

    async def notes(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.NoteView]:
        # Evidence can include owner-only documents; all notes deliberately remain owner-only.
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            query = (
                select(KnowledgeNote, KnowledgeNoteReview.decision)
                .outerjoin(KnowledgeNoteReview, KnowledgeNote.id == KnowledgeNoteReview.note_id)
                .where(KnowledgeNote.workspace_id == wid)
            )
            if cursor:
                query = query.where(KnowledgeNote.id > cursor)
            rows = (await s.execute(query.order_by(KnowledgeNote.id).limit(limit + 1))).all()
            return Page(
                items=[
                    d.NoteView(
                        **d.NoteView.model_validate(r[0]).model_dump(exclude={"decision"}),
                        decision=cast(str | None, r[1]),
                    )
                    for r in rows[:limit]
                ],
                next_cursor=rows[limit - 1][0].id if len(rows) > limit else None,
            )
