"""Small PDF/DOCX originals. Transport bytes are never returned as MCP text."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from smm_gpt.domain.operations import DTO, IdempotencyToken

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 200_000
FileFormat = Literal["pdf", "docx"]


class SubmitFile(DTO):
    idempotency_key: IdempotencyToken
    brand_id: UUID
    filename: Annotated[str, Field(min_length=1, max_length=160)]
    format: FileFormat
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    content_base64: Annotated[str, Field(min_length=1, max_length=2_796_204, repr=False)]

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if any(ord(c) < 32 or c in '/\\:"<>|?*' for c in value) or value in {".", ".."}:
            raise ValueError("invalid_filename")
        return value


class FileReceipt(DTO):
    file_id: UUID


class FileView(DTO):
    id: UUID
    brand_id: UUID
    actor_id: UUID
    filename: str
    format: FileFormat
    byte_size: int
    content_hash: str
    state: str
    attempts: int
    error_code: str | None
    created_at: datetime


class ExtractionView(DTO):
    text: str
    text_hash: str
    parser_version: str
    scan_engine: str
    signature_version: str
    signatures_updated_at: datetime
    scanned_at: datetime


class FileDetail(FileView):
    extraction: ExtractionView | None = None
    warning: str = (
        "Unreviewed extraction; not active knowledge, verified facts or malware-free guarantee."
    )


class RetryFile(DTO):
    idempotency_key: IdempotencyToken
    file_id: UUID
    expected_attempts: Annotated[int, Field(ge=0, le=3)]


class RescanFile(DTO):
    idempotency_key: IdempotencyToken
    file_id: UUID
