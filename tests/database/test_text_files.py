"""New originals retain the existing scan, human import, RLS and activation boundaries."""

import asyncio
import base64
import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command as migration
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from smm_gpt.core.config import Settings
from smm_gpt.domain import knowledge as k
from smm_gpt.domain import knowledge_files as d
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.file_models import KnowledgeFile
from smm_gpt.infrastructure.file_storage import VolumeFileStore
from smm_gpt.infrastructure.knowledge_models import KnowledgeVersion
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.parsers.documents import extract, parser_version
from smm_gpt.services.file_parser import ParsedFile
from smm_gpt.services.knowledge import KnowledgeService
from smm_gpt.services.knowledge_files import KnowledgeFileService
from smm_gpt.workers.knowledge_files import process

from ..file_fixtures import TEXT_FILES
from .conftest import TenantFixture
from .test_knowledge import activate, seed
from .test_knowledge_files import Scanner

pytestmark = pytest.mark.integration


def text_command(bid: UUID, format: d.FileFormat, data: bytes) -> d.SubmitFile:
    return d.SubmitFile(
        idempotency_key=uuid4().hex,
        brand_id=bid,
        filename="synthetic." + format,
        format=format,
        content_hash=hashlib.sha256(data).hexdigest(),
        content_base64=base64.b64encode(data).decode(),
    )


class TestOnlyParser:
    """In-process double ONLY in tests; real seccomp tested separately on Linux."""

    calls = 0

    async def parse(self, data: bytes, format: d.FileFormat) -> ParsedFile:
        self.calls += 1
        try:
            return ParsedFile(extract(data, format), parser_version(format))
        except ValueError as exc:
            raise OperationError(str(exc)) from None


@pytest.mark.parametrize("format,data,expected", TEXT_FILES)
async def test_text_original_to_owner_import_to_separate_activation(
    tenants: TenantFixture, tmp_path: Path, format: d.FileFormat, data: bytes, expected: str
) -> None:
    t, store = tenants, VolumeFileStore(tmp_path)
    service = KnowledgeFileService(
        t.access, Settings(_env_file=None, knowledge_files_enabled=True), store
    )
    bid = await seed(t)
    c = text_command(bid, format, data)
    with pytest.raises(OperationError, match="binary_ingestion_disabled"):
        await KnowledgeFileService(t.access, Settings(_env_file=None), store).submit(
            t.owner, t.workspace, c, uuid4()
        )
    receipt = await service.submit(t.owner, t.workspace, c, uuid4())
    assert await service.submit(t.owner, t.workspace, c, uuid4()) == receipt
    scanner, parser = Scanner(), TestOnlyParser()
    assert await process(
        t.worker, store, scanner, parser, t.workspace, receipt.file_id, t.owner.user_id
    )
    assert scanner.calls == parser.calls == 1
    detail = await service.read(t.owner, t.workspace, receipt.file_id, uuid4())
    assert detail.state == "ready" and detail.extraction
    assert expected in detail.extraction.text
    assert detail.extraction.parser_version == parser_version(format)
    assert (await service.download(t.owner, t.workspace, receipt.file_id, uuid4())) == (
        data,
        format,
    )
    with pytest.raises(AccessDenied):
        await service.read(t.viewer, t.workspace, receipt.file_id, uuid4())
    async with t.runtime.transaction(t.other.user_id, t.other_workspace) as s:
        assert await s.scalar(select(KnowledgeFile.id)) is None
    core = KnowledgeService(t.access, store)
    query = k.SearchRequest(query="крем", brand_id=bid)
    assert not (await core.search(t.owner, t.workspace, query, uuid4())).citations
    imported = k.ImportFile(
        idempotency_key=uuid4().hex,
        file_id=receipt.file_id,
        text_hash=detail.extraction.text_hash,
        brand_id=bid,
        title="Synthetic reference",
        source_date=utcnow(),
        effective_from=utcnow() - timedelta(days=1),
        effective_to=utcnow() + timedelta(days=1),
        human_confirmed=True,
    )
    with pytest.raises(OperationError, match="extraction_hash_mismatch"):
        await core.execute(
            t.owner, t.workspace, imported.model_copy(update={"text_hash": "0" * 64}), uuid4()
        )
    result = await core.execute(t.owner, t.workspace, imported, uuid4())
    assert not (await core.search(t.owner, t.workspace, query, uuid4())).citations
    async with t.admin.transaction() as s:
        version = await s.scalar(
            select(KnowledgeVersion).where(KnowledgeVersion.source_file_id == receipt.file_id)
        )
        assert (
            version and version.original == detail.extraction.text and version.format == "markdown"
        )
        # HTML/CSV syntax is NOT sent through an in-process parser a second time.
    await activate(t, result)
    citations = (await core.search(t.viewer, t.workspace, query, uuid4())).citations
    assert citations and citations[0].source_file_id == receipt.file_id
    assert "Крем" in citations[0].text
    with pytest.raises(DBAPIError, match="text_file_history_requires_restore_plan"):
        await asyncio.to_thread(migration.downgrade, Config("alembic.ini"), "0017_plan_adoption")
    async with t.admin.transaction() as s:
        assert await s.scalar(text("SELECT version_num FROM alembic_version")) == "0018_text_files"
    assert (await service.download(t.owner, t.workspace, receipt.file_id, uuid4()))[0] == data


async def test_scan_precedes_text_parser_and_unsafe_text_cannot_retry_or_import(
    tenants: TenantFixture, tmp_path: Path
) -> None:
    t, store = tenants, VolumeFileStore(tmp_path)
    service = KnowledgeFileService(
        t.access, Settings(_env_file=None, knowledge_files_enabled=True), store
    )
    bid = await seed(t)
    scanner, parser = Scanner(), TestOnlyParser()
    cases: list[tuple[d.FileFormat, bytes, str]] = [
        ("html", b"<script>synthetic-private-text</script>", "active_document_rejected"),
        ("csv", b"a,b\nsynthetic-private-text", "csv_row_width_invalid"),
        ("markdown", b"inert text", "scanner_unavailable"),
    ]
    for format, data, code in cases:
        c = text_command(bid, format, data)
        receipt = await service.submit(t.owner, t.workspace, c, uuid4())
        scanner.error = "scanner_unavailable"
        calls = parser.calls
        assert not await process(
            t.worker, store, scanner, parser, t.workspace, receipt.file_id, t.owner.user_id
        )
        assert parser.calls == calls
        if format != "markdown":
            scanner.error = None
            await service.retry(
                t.owner,
                t.workspace,
                d.RetryFile(
                    idempotency_key=uuid4().hex, file_id=receipt.file_id, expected_attempts=1
                ),
                uuid4(),
            )
            assert not await process(
                t.worker, store, scanner, parser, t.workspace, receipt.file_id, t.owner.user_id
            )
        detail = await service.read(t.owner, t.workspace, receipt.file_id, uuid4())
        assert detail.state == "failed" and detail.error_code == code and detail.extraction is None
        assert "synthetic-private-text" not in detail.model_dump_json()
        assert store.get(receipt.file_id, c.content_hash) == data
        if format != "markdown":
            with pytest.raises(OperationError, match="file_retry_not_allowed"):
                await service.retry(
                    t.owner,
                    t.workspace,
                    d.RetryFile(
                        idempotency_key=uuid4().hex, file_id=receipt.file_id, expected_attempts=2
                    ),
                    uuid4(),
                )
        with pytest.raises(OperationError, match="file_not_ready"):
            await service.download(t.owner, t.workspace, receipt.file_id, uuid4())
    # Failed originals still prohibit rollback; never drop their history to run old code.
    with pytest.raises(DBAPIError, match="text_file_history_requires_restore_plan"):
        await asyncio.to_thread(migration.downgrade, Config("alembic.ini"), "0017_plan_adoption")
