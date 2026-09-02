"""Bounded subprocess transport; no production in-process parsing fallback."""

import asyncio
import json
import os
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from smm_gpt.domain.knowledge_files import MAX_TEXT_BYTES, FileFormat
from smm_gpt.domain.operations import OperationError
from smm_gpt.services.knowledge_text import safe_text


@dataclass(frozen=True)
class ParsedFile:
    text: str
    parser_version: str


class Parser(Protocol):
    async def parse(self, data: bytes, format: FileFormat) -> ParsedFile: ...


class SandboxedParser:
    async def parse(self, data: bytes, format: FileFormat) -> ParsedFile:
        if sys.platform != "linux":
            raise OperationError("sandbox_unavailable", 503)
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-m",
                "smm_gpt.parsers.child",
                format,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
                env={"LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"},
            )
        except OSError:
            raise OperationError("sandbox_unavailable", 503) from None
        try:
            assert process.stdin and process.stdout
            async with asyncio.timeout(15):
                try:
                    process.stdin.write(data)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    # A failed lockdown may exit before consuming input; still read its verdict.
                    pass
                process.stdin.close()
                raw = b""
                while chunk := await process.stdout.read(65536):
                    raw += chunk
                    if len(raw) > MAX_TEXT_BYTES * 2 + 1000:
                        raise OperationError("parser_output_invalid")
                await process.wait()
            if process.returncode != 0:
                raise OperationError("parser_resource_limit")
            output = json.loads(raw)
            if not isinstance(output, dict):
                raise ValueError
            if output.get("error") == "sandbox_unavailable":
                raise OperationError("sandbox_unavailable", 503)
            if output.get("error"):
                code = output["error"]
                allowed = {
                    "extracted_text_too_large",
                    "extracted_text_empty",
                    "docx_archive_invalid",
                    "docx_expansion_limit",
                    "active_document_rejected",
                    "encrypted_or_oversized_pdf",
                    "pdf_object_limit",
                    "pdf_stream_limit",
                    "ocr_required",
                    "file_type_mismatch",
                }
                raise OperationError(
                    code if isinstance(code, str) and code in allowed else "document_rejected"
                )
            if set(output) != {"text", "parser_version"} or not all(
                isinstance(x, str) for x in output.values()
            ):
                raise ValueError
            safe_text(output["text"])
            if len(output["parser_version"]) > 100:
                raise ValueError
            return ParsedFile(output["text"], output["parser_version"])
        except TimeoutError:
            raise OperationError("parser_timeout", 503) from None
        except (ValueError, OSError):
            raise OperationError("parser_output_invalid") from None
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
