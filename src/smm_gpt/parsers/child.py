"""Fixed child entry point. No application settings, credentials or domain principal."""

import codecs
import json
import sys

# Load trusted parser modules BEFORE lockdown; input remains unread until restrict succeeds.
from smm_gpt.parsers.documents import MAX_INPUT, PARSER_VERSION, extract
from smm_gpt.parsers.sandbox import restrict

for encoding in (
    "utf-16-be",
    "utf-16-le",
    "utf-16",
    "latin-1",
    "cp1252",
    "cp437",  # ZIP member names use this even when every filename is ASCII.
    "ascii",
    "unicode-escape",
):
    codecs.lookup(encoding)


def main() -> None:
    try:
        restrict()
    except Exception:
        print('{"error":"sandbox_unavailable"}')
        return
    try:
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        value = extract(data, sys.argv[1])
        result = {"text": value, "parser_version": PARSER_VERSION}
    except Exception as exc:
        # Only our fixed error codes, never library exceptions, original bytes or paths.
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
        result = {
            "error": str(exc)
            if type(exc) is ValueError and str(exc) in allowed
            else "document_rejected"
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
