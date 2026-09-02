"""Deterministic text-only ingestion; no evaluation, URL access, formulas or active HTML."""

import csv
import io
import re
import unicodedata
from html.parser import HTMLParser

from smm_gpt.domain.operations import OperationError

SECRET = re.compile(
    r"-----BEGIN .*PRIVATE KEY-----|\b(?:password|passwd|authorization|api[_-]?key|"
    r"access[_-]?token|client[_-]?secret)\s*[:=]|\bBearer\s+\S+|"
    r"\b(?:sk|ghp|github_pat)[_-][A-Za-z0-9_-]{16,}|postgres(?:ql)?://",
    re.IGNORECASE,
)


def safe_text(value: str) -> str:
    if len(value.encode()) > 200_000 or "\x00" in value or SECRET.search(value):
        raise OperationError("unsafe_or_oversized_text", 422)
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise OperationError("binary_input_not_supported", 422)
    return value


class TextHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Reject, rather than attempting to sanitize nested active content.
        if tag in {"script", "style", "iframe", "object", "embed", "svg", "math", "form"}:
            raise OperationError("active_html_rejected", 422)
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalize(original: str, format: str) -> str:
    value = safe_text(original)
    if format == "html":
        parser = TextHTML()
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts)
    elif format == "csv":
        try:
            rows = list(csv.reader(io.StringIO(value), strict=True))
        except csv.Error:
            raise OperationError("invalid_csv", 422) from None
        if len(rows) > 1000 or any(len(row) > 30 for row in rows):
            raise OperationError("csv_limit_exceeded", 422)
        # Plain text only. This does not import metrics or evaluate spreadsheet formulas.
        value = "\n\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
    elif format != "markdown":
        raise OperationError("binary_parser_not_available", 422)
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    safe_text(value)
    if not value:
        raise OperationError("empty_document", 422)
    return value


def chunks(value: str) -> list[tuple[str, str]]:
    """Section/paragraph boundaries, deterministic fallback for unusually long paragraphs."""
    result: list[tuple[str, str]] = []
    section = ""
    for paragraph in re.split(r"\n\s*\n", value):
        lines = paragraph.splitlines()
        if lines and lines[0].startswith("#"):
            section = lines[0].lstrip("# ")[:200]
        while len(paragraph) > 1800:
            end = paragraph.rfind(" ", 900, 1800)
            if end < 900:
                end = 1800
            result.append((section, paragraph[:end]))
            paragraph = paragraph[end:].strip()
        if paragraph:
            result.append((section, paragraph))
    if not result or len(result) > 250:
        raise OperationError("chunk_limit_exceeded", 422)
    return result
