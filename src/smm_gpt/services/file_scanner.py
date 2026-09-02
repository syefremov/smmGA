"""Private clamd INSTREAM client; only an exact clean reply from fresh signatures passes."""

import asyncio
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from smm_gpt.domain.operations import OperationError


@dataclass(frozen=True)
class ScanEvidence:
    engine: str
    signature_version: str
    signatures_updated_at: datetime
    scanned_at: datetime


class Scanner(Protocol):
    async def scan(self, data: bytes) -> ScanEvidence: ...


def version_evidence(value: bytes, now: datetime) -> ScanEvidence:
    try:
        engine, version, stamp = value.decode("ascii").rstrip("\0").split("/")
        if not re.fullmatch(
            r"ClamAV [0-9]+\.[0-9]+\.[0-9]+(?:[.-][a-zA-Z0-9]+)*", engine
        ) or not re.fullmatch(r"[0-9]{1,12}", version):
            raise ValueError
        updated = datetime.strptime(stamp, "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
    except (ValueError, UnicodeError):
        raise OperationError("scanner_unavailable", 503) from None
    if not now - timedelta(hours=48) <= updated <= now + timedelta(minutes=10):
        raise OperationError("scanner_signatures_stale", 503)
    return ScanEvidence(engine, version, updated, now)


class ClamScanner:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port

    async def _exchange(self, command: bytes, data: bytes | None = None) -> bytes:
        reader, writer = await asyncio.open_connection(self.host, self.port, limit=1024)
        try:
            writer.write(command)
            if data is not None:
                for offset in range(0, len(data), 65536):
                    chunk = data[offset : offset + 65536]
                    writer.write(struct.pack("!I", len(chunk)) + chunk)
                    await writer.drain()
                writer.write(b"\0\0\0\0")
            await writer.drain()
            result = await reader.readuntil(b"\0")
            if len(result) > 1024:
                raise OperationError("scanner_unavailable", 503)
            return result
        finally:
            writer.close()
            await writer.wait_closed()

    async def scan(self, data: bytes) -> ScanEvidence:
        try:
            async with asyncio.timeout(30):
                before = version_evidence(await self._exchange(b"zVERSION\0"), datetime.now(UTC))
                result = await self._exchange(b"zINSTREAM\0", data)
                if result.endswith(b" FOUND\0"):
                    raise OperationError("malware_detected")
                if result != b"stream: OK\0":
                    raise OperationError("scanner_unavailable", 503)
                after = version_evidence(await self._exchange(b"zVERSION\0"), datetime.now(UTC))
                # Don't attribute a scan to a signature set which changed mid-call.
                if (before.engine, before.signature_version) != (
                    after.engine,
                    after.signature_version,
                ):
                    raise OperationError("scanner_unavailable", 503)
                return after
        except OperationError:
            raise
        except Exception:
            raise OperationError("scanner_unavailable", 503) from None
