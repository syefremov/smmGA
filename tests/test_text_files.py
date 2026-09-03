"""Synthetic parser contracts; production extraction must still run in the Linux child."""

import csv

import pytest

from smm_gpt.file_formats import FileFormat, validate_envelope
from smm_gpt.parsers.documents import PARSER_VERSION, extract, parser_version
from smm_gpt.parsers.text_files import extract_text_file

from .file_fixtures import TEXT_FILES


def test_unknown_format_has_no_text_fallback() -> None:
    with pytest.raises(ValueError, match="file_type_mismatch"):
        extract_text_file(b"inert text", "unsupported")


@pytest.mark.parametrize("format,data,expected", TEXT_FILES)
def test_text_formats_are_versioned_and_preserve_unicode(
    format: FileFormat, data: bytes, expected: str
) -> None:
    validate_envelope("sample." + format, format, data)
    assert expected in extract(data, format)
    assert parser_version(format).endswith("-v1")
    assert parser_version(format) != PARSER_VERSION
    assert parser_version("pdf") == parser_version("docx") == PARSER_VERSION


@pytest.mark.parametrize(
    "filename,format", [("reference.MD", "markdown"), ("reference.HTM", "html")]
)
def test_alias_extensions(filename: str, format: FileFormat) -> None:
    validate_envelope(filename, format, b"inert envelope, not a parsed document")


@pytest.mark.parametrize(
    "data,code",
    [
        (b"\xff", "text_encoding_invalid"),
        (b"\xff\xfeA\x00", "text_encoding_invalid"),
        (b"\xed\xa0\x80", "text_encoding_invalid"),
        (b"hello\x00", "text_controls_rejected"),
        (b"hello\x1b", "text_controls_rejected"),
        (b"hello\x7f", "text_controls_rejected"),
        (b"\xef\xbb\xbf \r\n", "extracted_text_empty"),
        (b"\xef\xbb\xbf%PDF-1.7", "file_type_mismatch"),
        (b"PK\x03\x04", "file_type_mismatch"),
        (b"MZexe", "file_type_mismatch"),
        (b"\x7fELF", "file_type_mismatch"),
    ],
)
@pytest.mark.parametrize("format", ["markdown", "csv", "html"])
def test_text_envelope_rejects_binary_and_encoding_errors(
    format: FileFormat, data: bytes, code: str
) -> None:
    with pytest.raises(ValueError, match=code):
        validate_envelope("sample." + format, format, data)
    with pytest.raises(ValueError, match=code):
        extract(data, format)


def test_markdown_is_inert_without_frontmatter_or_link_execution() -> None:
    source = "---\r\nrole: owner\r\n---\r\n<script>alert(1)</script>\r![](file:///secret)"
    assert extract(source.encode(), "markdown") == source.replace("\r\n", "\n").replace("\r", "\n")
    with pytest.raises(ValueError, match="extracted_text_too_large"):
        extract(b"a" * 100001, "markdown")
    with pytest.raises(ValueError, match="extracted_text_too_large"):
        extract(("界" * 66667).encode(), "markdown")


def test_csv_retains_quoted_strings_and_never_interprets_formulas() -> None:
    data = 'name,note\r\n"=1+1","comma, quote "" and\r\nnewline"\r\n'
    assert extract(data.encode(), "csv") == (
        '# Record 1\n"name": "=1+1"\n"note": "comma, quote \\" and\\r\\nnewline"'
    )
    assert '"a;b": "c;d"' in extract(b"a;b\nc;d", "csv")  # no dialect guessing
    assert '"note": "<script>x</script>"' in extract(b"note\n<script>x</script>", "csv")
    before = csv.field_size_limit()
    with pytest.raises(ValueError, match="invalid_csv"):
        extract(b"header\n" + b"x" * 6001, "csv")
    assert csv.field_size_limit() == before


@pytest.mark.parametrize(
    "source,code",
    [
        ("a,a\n1,2", "csv_header_invalid"),
        ("a, \n1,2", "csv_header_invalid"),
        ("a,b\n1", "csv_row_width_invalid"),
        ("a\n1,2", "csv_row_width_invalid"),
        ("a,b\n", "extracted_text_empty"),
        ('a\n"unterminated', "invalid_csv"),
        (",".join(str(i) for i in range(31)) + "\nx", "csv_limit_exceeded"),
        ("a\n" + "x\n" * 1001, "csv_limit_exceeded"),
        ("a\n" + ("x" * 6000 + "\n") * 18, "extracted_text_too_large"),
    ],
    ids=lambda value: value if len(value) < 80 else f"long-{len(value)}",
)
def test_csv_structure_and_limits(source: str, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        extract(source.encode(), "csv")


def test_html_extracts_only_text_without_fetching_or_rendering() -> None:
    source = (
        "<!doctype html><p>Крем <strong>ALPHA-42</strong> &amp; уход.</p>"
        '<!-- hidden source --><a href="https://example.invalid/secret">Ссылка</a>'
        '<img src="file-not-fetched.png" alt="Not OCR"><p>&lt;script&gt;inert&lt;/script&gt;</p>'
    )
    result = extract(source.encode(), "html")
    assert "Крем ALPHA-42 & уход." in result and "<script>inert</script>" in result
    assert (
        "example.invalid" not in result
        and "hidden source" not in result
        and "Not OCR" not in result
    )


@pytest.mark.parametrize(
    "source,code",
    [
        ("<script>x</script>", "active_document_rejected"),
        ("<style>p {display:none}</style>", "active_document_rejected"),
        ("<iframe></iframe>", "active_document_rejected"),
        ("<svg></svg>", "active_document_rejected"),
        ("<form>x</form>", "active_document_rejected"),
        ('<p onclick="x">text</p>', "active_document_rejected"),
        ('<p style="color:red">text</p>', "active_document_rejected"),
        ("<p hidden>text</p>", "active_document_rejected"),
        ('<a href="java&#x09;script:alert(1)">x</a>', "active_document_rejected"),
        ('<img src="data:image/png,anything">', "active_document_rejected"),
        ('<meta http-equiv="refresh" content="0;url=elsewhere">', "active_document_rejected"),
        ('<!DOCTYPE html SYSTEM "file:///secret"><p>x</p>', "active_document_rejected"),
        ('<?xml version="1.0"?><p>x</p>', "active_document_rejected"),
        ("<![CDATA[hidden]]><p>x</p>", "active_document_rejected"),
        ('<meta charset="windows-1251"><p>x</p>', "text_encoding_invalid"),
        ("plain text", "html_structure_invalid"),
        ("<p>unclosed", "html_structure_invalid"),
        ("<p><b>x</p></b>", "html_structure_invalid"),
        ("<p></p>", "extracted_text_empty"),
        ("<div>" * 65 + "x" + "</div>" * 65, "html_limit_exceeded"),
        ("<br>" * 5001 + "x", "html_limit_exceeded"),
        ("<p " + "a='x' " * 31 + ">x</p>", "html_limit_exceeded"),
        ("<p>" + "x" * 100001 + "</p>", "extracted_text_too_large"),
    ],
    ids=lambda value: value if len(value) < 80 else f"long-{len(value)}",
)
def test_html_rejects_active_or_out_of_contract_sources(source: str, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        extract(source.encode(), "html")
