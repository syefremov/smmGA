import asyncio
import threading
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from smm_gpt.core.config import Settings
from smm_gpt.domain import ingestion as d
from smm_gpt.domain import knowledge as k
from smm_gpt.domain.knowledge_files import FileFormat, RetryFile
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.file_models import KnowledgeExtraction, KnowledgeFile
from smm_gpt.infrastructure.file_storage import VolumeFileStore
from smm_gpt.infrastructure.knowledge_models import KnowledgeChunk, KnowledgeIndex
from smm_gpt.infrastructure.models import Identity, Membership, utcnow
from smm_gpt.services.file_parser import ParsedFile
from smm_gpt.services.ingestion import IngestionService
from smm_gpt.services.ingestion_state import reconcile
from smm_gpt.services.knowledge import KnowledgeService
from smm_gpt.services.knowledge_files import KnowledgeFileService
from smm_gpt.services.knowledge_text import chunks
from smm_gpt.workers.knowledge import process as index_process
from smm_gpt.workers.knowledge_files import process as file_process

from .conftest import TenantFixture
from .test_knowledge import activate, seed, submit
from .test_knowledge_files import Parser, Scanner, command

pytestmark = pytest.mark.integration


async def prepare(
    t: TenantFixture, path: Path
) -> tuple[UUID, UUID, VolumeFileStore, KnowledgeFileService]:
    bid = await seed(t)
    doc, _ = await submit(t, bid)
    assert doc.index_id
    store = VolumeFileStore(path)
    files = KnowledgeFileService(
        t.access, Settings(_env_file=None, knowledge_files_enabled=True), store
    )
    fid = (await files.submit(t.owner, t.workspace, command(bid), uuid4())).file_id
    return doc.index_id, fid, store, files


@pytest.mark.parametrize("kind", ["index", "file"])
async def test_cancel_queued_private_idempotent_and_immutable(
    tenants: TenantFixture,
    tmp_path: Path,
    kind: d.JobKind,
) -> None:
    t = tenants
    iid, fid, store, _ = await prepare(t, tmp_path)
    jid = iid if kind == "index" else fid
    service = IngestionService(t.access)
    c = d.CancelIngestion(kind=kind, job_id=jid, expected_version=1, idempotency_key=uuid4().hex)
    receipts = await asyncio.gather(
        *(service.cancel(t.owner, t.workspace, c, uuid4()) for _ in range(3))
    )
    assert all(r == receipts[0] for r in receipts) and receipts[0].version == 2
    jobs = (await service.jobs(t.owner, t.workspace, kind, uuid4())).items
    assert len(jobs) == 1 and jobs[0].state == "cancelled" and jobs[0].finished_at
    assert jobs[0].attempts == 0
    history = await service.history(t.owner, t.workspace, kind, jid, uuid4())
    assert [e.state for e in history.events] == ["queued", "cancelled"]
    assert [e.version for e in history.events] == [1, 2] and not history.truncated
    scanner, parser = Scanner(), Parser()
    assert (
        not await index_process(t.worker, t.workspace, iid, t.owner.user_id)
        if kind == "index"
        else not await file_process(
            t.worker, store, scanner, parser, t.workspace, fid, t.owner.user_id
        )
    )
    assert scanner.calls == parser.calls == 0
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await service.cancel(
            t.owner, t.workspace, c.model_copy(update={"expected_version": 2}), uuid4()
        )
    with pytest.raises(OperationError, match="ingestion_cancel_not_allowed"):
        await service.cancel(
            t.owner,
            t.workspace,
            c.model_copy(update={"expected_version": 2, "idempotency_key": uuid4().hex}),
            uuid4(),
        )
    # Editor cannot control another actor's queue, even a workspace-visible text document.
    async with t.admin.transaction() as s:
        member = await s.scalar(select(Membership).where(Membership.user_id == t.viewer.user_id))
        assert member
        member.role = "editor"
    assert not (await service.jobs(t.viewer, t.workspace, kind, uuid4())).items
    with pytest.raises(OperationError, match="not_found"):
        await service.cancel(t.viewer, t.workspace, c, uuid4())
    with pytest.raises(OperationError, match="not_found"):
        await service.history(t.viewer, t.workspace, kind, jid, uuid4())
    table = "knowledge_indexes" if kind == "index" else "knowledge_files"
    for sql in (
        "SELECT smm_ingestion_reconcile('index')",
        f"UPDATE {table} SET attempts=attempts+1",
        f"UPDATE {table} SET state='queued',version=version+1",
        "UPDATE knowledge_job_receipts SET result='{}'",
        "INSERT INTO knowledge_job_events DEFAULT VALUES",
        "UPDATE knowledge_job_events SET state='ready'",
    ):
        with pytest.raises(DBAPIError):
            async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(sql))
    with pytest.raises(DBAPIError):
        async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
            await s.execute(text("INSERT INTO knowledge_job_receipts DEFAULT VALUES"))
    async with t.admin.transaction() as s:
        file = await s.get(KnowledgeFile, fid)
        assert file and store.get(fid, file.content_hash)


@pytest.mark.parametrize("kind", ["index", "file"])
async def test_cancel_processing_fences_late_output(
    tenants: TenantFixture,
    tmp_path: Path,
    kind: d.JobKind,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = tenants
    iid, fid, store, files = await prepare(t, tmp_path)
    entered, release = threading.Event(), threading.Event()

    def paused(value: str) -> list[tuple[str, str]]:
        entered.set()
        assert release.wait(5)
        return chunks(value)

    class PausedParser(Parser):
        async def parse(self, data: bytes, format: FileFormat) -> ParsedFile:
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
            return await super().parse(data, format)

    monkeypatch.setattr("smm_gpt.workers.knowledge.chunks", paused)
    scanner = Scanner()
    jid = iid if kind == "index" else fid
    task = asyncio.create_task(
        index_process(t.worker, t.workspace, iid, t.owner.user_id)
        if kind == "index"
        else file_process(
            t.worker, store, scanner, PausedParser(), t.workspace, fid, t.owner.user_id
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        service = IngestionService(t.access)
        with pytest.raises(OperationError, match="ingestion_conflict"):
            await service.cancel(
                t.owner,
                t.workspace,
                d.CancelIngestion(
                    kind=kind, job_id=jid, expected_version=1, idempotency_key=uuid4().hex
                ),
                uuid4(),
            )
        result = await service.cancel(
            t.owner,
            t.workspace,
            d.CancelIngestion(
                kind=kind, job_id=jid, expected_version=2, idempotency_key=uuid4().hex
            ),
            uuid4(),
        )
        assert result.version == 3
    finally:
        release.set()
    assert not await task
    assert await reconcile(t.worker, kind) == 0
    async with t.admin.transaction() as s:
        assert await s.scalar(select(KnowledgeChunk.id)) is None
        assert await s.scalar(select(KnowledgeExtraction.id)) is None
    if kind == "file":
        with pytest.raises(OperationError, match="file_retry_not_allowed"):
            await files.retry(
                t.owner,
                t.workspace,
                RetryFile(idempotency_key=uuid4().hex, file_id=fid, expected_attempts=1),
                uuid4(),
            )


@pytest.mark.parametrize("kind", ["index", "file"])
async def test_interrupted_jobs_require_explicit_fresh_processing(
    tenants: TenantFixture,
    tmp_path: Path,
    kind: d.JobKind,
) -> None:
    t = tenants
    iid, fid, store, files = await prepare(t, tmp_path)
    table = "knowledge_indexes" if kind == "index" else "knowledge_files"
    jid = iid if kind == "index" else fid
    # Simulate an interrupted worker after a committed reservation, not a manual reset.
    async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
        await s.execute(
            text(f"""UPDATE {table} SET state='processing',version=version+1,
            attempts=1,lease_id=:lease,lease_until=now()-interval '1 second',started_at=now()
            WHERE id=:id"""),
            {"id": jid, "lease": uuid4()},
        )
    pending = "smm_knowledge_pending" if kind == "index" else "smm_files_pending"
    async with t.worker.transaction() as s:
        assert not (await s.execute(text(f"SELECT * FROM {pending}()"))).all()
    counts = await asyncio.gather(reconcile(t.worker, kind), reconcile(t.worker, kind))
    assert sum(counts) == 1 and await reconcile(t.worker, kind) == 0
    row = (await IngestionService(t.access).jobs(t.owner, t.workspace, kind, uuid4())).items[0]
    assert row.state == "failed" and row.error_code == "processing_interrupted" and row.version == 3
    history = await IngestionService(t.access).history(t.owner, t.workspace, kind, jid, uuid4())
    assert history.events[-1].actor_id is None
    assert history.events[-1].error_code == "processing_interrupted"
    scanner, parser = Scanner(), Parser()
    if kind == "file":
        await files.retry(
            t.owner,
            t.workspace,
            RetryFile(idempotency_key=uuid4().hex, file_id=fid, expected_attempts=1),
            uuid4(),
        )
        assert await file_process(
            t.worker, store, scanner, parser, t.workspace, fid, t.owner.user_id
        )
        ready = await files.read(t.owner, t.workspace, fid, uuid4())
        assert ready.version == 6 and ready.attempts == 2 and ready.extraction
        assert scanner.calls == parser.calls == 1
        history = await IngestionService(t.access).history(t.owner, t.workspace, kind, jid, uuid4())
        assert [e.state for e in history.events] == [
            "queued",
            "processing",
            "failed",
            "queued",
            "processing",
            "ready",
        ]
        assert history.events[2].error_code == "processing_interrupted"
    else:
        core = KnowledgeService(t.access)
        assert row.document_id
        doc = await core.read_document(t.owner, t.workspace, row.document_id, uuid4())
        retry = await core.execute(
            t.owner,
            t.workspace,
            k.ReindexDocument(
                idempotency_key=uuid4().hex,
                document_id=doc.id,
                expected_version=doc.version,
                document_version_id=doc.indexes[0].document_version_id,
            ),
            uuid4(),
        )
        assert retry.index_id and retry.index_id != iid
        assert await index_process(t.worker, t.workspace, retry.index_id, t.owner.user_id)
        doc = await core.read_document(t.owner, t.workspace, doc.id, uuid4())
        assert doc.active_index_id is None
        assert {i.state for i in doc.indexes} == {"failed", "ready"}


async def test_reconcile_revocation_expiry_bounds_and_privileges(
    tenants: TenantFixture,
    tmp_path: Path,
) -> None:
    t = tenants
    iid, fid, _, _ = await prepare(t, tmp_path)
    # Extra old jobs have no originals, and must be expired without accessing storage.
    async with t.admin.transaction() as s:
        old = await s.get(KnowledgeFile, fid)
        assert old
        for _ in range(11):
            s.add(
                KnowledgeFile(
                    workspace_id=t.workspace,
                    actor_id=t.owner.user_id,
                    identity_id=t.owner.identity_id,
                    brand_id=old.brand_id,
                    filename="old.docx",
                    format="docx",
                    content_hash=old.content_hash,
                    byte_size=old.byte_size,
                    key_hash=uuid4().hex,
                    request_hash=uuid4().hex,
                    created_at=utcnow() - timedelta(days=2),
                )
            )
    assert await reconcile(t.worker, "file") == 10
    assert await reconcile(t.worker, "file") == 1
    async with t.admin.transaction() as s:
        identity = await s.get(Identity, t.owner.identity_id)
        assert identity
        identity.active = False
    assert await reconcile(t.worker, "index") == 1
    assert await reconcile(t.worker, "file") == 1
    async with t.admin.transaction() as s:
        index = await s.get(KnowledgeIndex, iid)
        file = await s.get(KnowledgeFile, fid)
        assert index and file
        assert index.error_code == file.error_code == "authorization_changed"
        assert index.attempts == file.attempts == 0
    with pytest.raises(DBAPIError):
        async with t.worker.transaction() as s:
            await s.execute(text("SELECT smm_ingestion_reconcile('all')"))


async def test_cancel_preserves_active_index_and_archive_closes_pending(
    tenants: TenantFixture,
) -> None:
    t = tenants
    bid = await seed(t)
    original, _ = await submit(t, bid)
    await activate(t, original)
    core = KnowledgeService(t.access)
    jobs = IngestionService(t.access)
    doc = await core.read_document(t.owner, t.workspace, original.entity_id, uuid4())
    reindex = k.ReindexDocument(
        idempotency_key=uuid4().hex,
        document_id=doc.id,
        expected_version=doc.version,
        document_version_id=doc.indexes[0].document_version_id,
    )
    pending = await core.execute(t.owner, t.workspace, reindex, uuid4())
    assert pending.index_id
    await jobs.cancel(
        t.owner,
        t.workspace,
        d.CancelIngestion(
            idempotency_key=uuid4().hex, kind="index", job_id=pending.index_id, expected_version=1
        ),
        uuid4(),
    )
    assert (
        await core.read_document(t.owner, t.workspace, doc.id, uuid4())
    ).active_index_id == original.index_id
    found = await core.search(
        t.owner, t.workspace, k.SearchRequest(brand_id=bid, query="крем"), uuid4()
    )
    assert found.citations and all(c.index_id == original.index_id for c in found.citations)
    next_job = await core.execute(
        t.owner,
        t.workspace,
        reindex.model_copy(
            update={
                "idempotency_key": uuid4().hex,
                "expected_version": pending.version,
            }
        ),
        uuid4(),
    )
    await core.execute(
        t.owner,
        t.workspace,
        k.ArchiveDocument(
            idempotency_key=uuid4().hex, document_id=doc.id, expected_version=next_job.version
        ),
        uuid4(),
    )
    assert await reconcile(t.worker, "index") == 1
    history = (
        await jobs.history(t.owner, t.workspace, "index", next_job.index_id, uuid4())
        if next_job.index_id
        else None
    )
    assert history and history.events[-1].error_code == "document_unavailable"
    async with t.admin.transaction() as s:
        ready = await s.get(KnowledgeIndex, original.index_id)
        assert ready and ready.state == "ready"
