import asyncio
import hashlib
import json
import struct
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.file_storage import VolumeFileStore
from smm_gpt.parsers.documents import extract
from smm_gpt.services.file_parser import SandboxedParser
from smm_gpt.services.file_scanner import ClamScanner, version_evidence

from .file_fixtures import TEXT_FILES, docx, pdf


def test_extract_synthetic_documents() -> None:
    assert "Крем ALPHA-42" in extract(docx(), "docx")
    assert "Synthetic ALPHA-42" in extract(pdf(), "pdf")


@pytest.mark.parametrize(
    "data,format,code",
    [
        (pdf(blank=True), "pdf", "ocr_required"),
        (pdf(active=True), "pdf", "active_document_rejected"),
        (pdf(encrypted=True), "pdf", "encrypted_or_oversized_pdf"),
        (docx(extra={"../escape": b"x"}), "docx", "docx_archive_invalid"),
        (docx(extra={"word/vbaProject.bin": b"x"}), "docx", "active_document_rejected"),
        (
            docx(
                extra={
                    "word/_rels/document.xml.rels": (
                        b'<Relationships><Relationship TargetMode="External" '
                        b'Target="https://example.invalid"/></Relationships>'
                    )
                }
            ),
            "docx",
            "active_document_rejected",
        ),
        (
            docx(
                extra={
                    "word/extra.xml": b'<w:altChunk xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
                }
            ),
            "docx",
            "active_document_rejected",
        ),
        (docx(extra={"padding": b"x" * 50000}), "docx", "docx_expansion_limit"),
        (docx(extra={"[Content_Types].xml": b"<Types/>"}), "docx", "docx_archive_invalid"),
        (b"not a file", "pdf", "file_type_mismatch"),
    ],
)
def test_reject_unsafe_documents(data: bytes, format: str, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        extract(data, format)


def test_xml_entities_never_resolve() -> None:
    from defusedxml.common import DefusedXmlException

    data = docx(
        extra={
            "extra.xml": b'<!DOCTYPE x [<!ENTITY value SYSTEM "file:///etc/passwd">]><x>&value;</x>'
        }
    )
    with pytest.raises(DefusedXmlException):
        extract(data, "docx")


@pytest.mark.parametrize(
    "format,data,expected",
    [("docx", docx(), "Крем"), ("pdf", pdf(), "ALPHA-42"), *TEXT_FILES],
    ids=["docx", "pdf", "markdown", "csv", "html"],
)
def test_preloaded_parsers_need_no_file_opens(format: str, data: bytes, expected: str) -> None:
    # Portable regression for lazy stdlib codecs/imports, in addition to real Linux seccomp.
    code = """
import json, sys
from smm_gpt.parsers import child
def deny_open(event, args):
    if event == 'open':
        raise PermissionError('Unexpected file open after parser preload')
sys.addaudithook(deny_open)
print(json.dumps({'text': child.extract(sys.stdin.buffer.read(), sys.argv[1])}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, format], input=data, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert expected in json.loads(result.stdout)["text"]


def test_volume_write_once_and_integrity(tmp_path: Path) -> None:
    store, fid, value = VolumeFileStore(tmp_path), uuid4(), docx()
    checksum = hashlib.sha256(value).hexdigest()
    store.put(fid, value)
    assert store.get(fid, checksum) == value
    with pytest.raises(OperationError):
        store.put(fid, value)
    with pytest.raises(OperationError):
        store.get(fid, "0" * 64)
    with pytest.raises(OperationError):
        store.get(uuid4(), checksum)


def stamp(now: datetime, version: str = "12345") -> bytes:
    return f"ClamAV 1.4.3/{version}/{now.strftime('%a %b %d %H:%M:%S %Y')}\0".encode()


@pytest.mark.parametrize("offset", [-49 * 60, 11])
def test_signature_freshness(offset: int) -> None:
    now = datetime.now(UTC)
    with pytest.raises(OperationError, match="scanner_signatures_stale"):
        version_evidence(stamp(now + timedelta(minutes=offset)), now)


@pytest.mark.parametrize(
    "reply,code",
    [
        (b"stream: OK\0", None),
        (b"stream: Synthetic FOUND\0", "malware_detected"),
        (b"stream: ERROR\0", "scanner_unavailable"),
        (b"anything OK\0", "scanner_unavailable"),
    ],
)
async def test_clamd_protocol(reply: bytes, code: str | None) -> None:
    received = bytearray()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            command = await reader.readuntil(b"\0")
            if command == b"zVERSION\0":
                writer.write(stamp(datetime.now(UTC)))
            else:
                assert command == b"zINSTREAM\0"
                while size := struct.unpack("!I", await reader.readexactly(4))[0]:
                    received.extend(await reader.readexactly(size))
                writer.write(reply)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        scanner = ClamScanner("127.0.0.1", server.sockets[0].getsockname()[1])
        value = b"synthetic" * 10000
        if code:
            with pytest.raises(OperationError, match=code):
                await scanner.scan(value)
        else:
            result = await scanner.scan(value)
            assert result.engine == "ClamAV 1.4.3"
        assert received == value


async def test_actual_sandbox_or_fail_closed() -> None:
    parser = SandboxedParser()
    if sys.platform != "linux":
        with pytest.raises(OperationError, match="sandbox_unavailable"):
            await parser.parse(docx(), "docx")
        return
    assert "Крем" in (await parser.parse(docx(), "docx")).text
    assert "ALPHA-42" in (await parser.parse(pdf(), "pdf")).text
    for format, data, expected in TEXT_FILES:
        assert expected in (await parser.parse(data, format)).text
    with pytest.raises(OperationError, match="active_document_rejected"):
        await parser.parse(b"<script>x</script>", "html")
    with pytest.raises(OperationError, match="csv_row_width_invalid"):
        await parser.parse(b"a,b\n1", "csv")
    with pytest.raises(OperationError, match="active_document_rejected"):
        await parser.parse(pdf(active=True), "pdf")


async def test_scanner_reload_cannot_misattribute_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    scanner = ClamScanner("unused", 3310)
    replies = iter([stamp(datetime.now(UTC)), b"stream: OK\0", stamp(datetime.now(UTC), "67890")])

    async def exchange(command: bytes, data: bytes | None = None) -> bytes:
        return next(replies)

    monkeypatch.setattr(scanner, "_exchange", exchange)
    with pytest.raises(OperationError, match="scanner_unavailable"):
        await scanner.scan(b"synthetic")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux seccomp verified in CI")
def test_sandbox_denies_files_network_processes_and_excess_memory() -> None:
    code = """
import json, os, socket
from smm_gpt.parsers.sandbox import restrict
restrict()
denied = []
actions = (lambda: open('/etc/passwd'), lambda: socket.socket(),
           lambda: os.fork(), lambda: bytearray(512*1024*1024))
for action in actions:
    try:
        action()
        denied.append(False)
    except (PermissionError, MemoryError):
        denied.append(True)
print(json.dumps(denied))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, timeout=15, check=True
    )
    assert json.loads(result.stdout) == [True] * 4


@pytest.mark.skipif(sys.platform != "linux", reason="Linux CPU limit verified in CI")
def test_sandbox_cpu_limit() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from smm_gpt.parsers.sandbox import restrict\nrestrict()\nwhile True: pass",
        ],
        capture_output=True,
        timeout=12,
    )
    assert result.returncode == -9
