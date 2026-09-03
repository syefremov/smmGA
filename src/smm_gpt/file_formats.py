"""Pure envelope checks shared by upload and the isolated parser; no document parsing."""

from typing import Literal

FileFormat = Literal["pdf", "docx", "markdown", "csv", "html"]
FILE_EXTENSIONS: dict[FileFormat, tuple[str, ...]] = {
    "pdf": ("pdf",),
    "docx": ("docx",),
    "markdown": ("md", "markdown"),
    "csv": ("csv",),
    "html": ("html", "htm"),
}
MAX_FILE_BYTES = 2 * 1024 * 1024


def utf8_text(data: bytes) -> str:
    # Reject known binary envelopes; this is not general polyglot detection.
    if data.removeprefix(b"\xef\xbb\xbf").startswith((b"%PDF-", b"PK\x03\x04", b"MZ", b"\x7fELF")):
        raise ValueError("file_type_mismatch")
    try:
        value = data.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise ValueError("text_encoding_invalid") from None
    if any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 for c in value):
        raise ValueError("text_controls_rejected")
    if not value.strip():
        raise ValueError("extracted_text_empty")
    return value


def validate_envelope(filename: str, format: FileFormat, data: bytes) -> None:
    if not 0 < len(data) <= MAX_FILE_BYTES:
        raise ValueError("file_size_invalid")
    if format not in FILE_EXTENSIONS or not any(
        filename.casefold().endswith("." + ext) for ext in FILE_EXTENSIONS[format]
    ):
        raise ValueError("file_type_mismatch")
    if format in ("pdf", "docx"):
        if not data.startswith(b"%PDF-" if format == "pdf" else b"PK\x03\x04"):
            raise ValueError("file_type_mismatch")
    else:
        utf8_text(data)
