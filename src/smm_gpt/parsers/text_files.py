"""Bounded UTF-8 file extraction. Production use requires scan and child lockdown first."""

import csv
import io
import json
import re
from html.parser import HTMLParser

from smm_gpt.file_formats import utf8_text

TEXT_PARSER_VERSIONS = {
    "markdown": "markdown-utf8-v1",
    "csv": "csv-utf8-rows-v1",
    "html": "html-passive-utf8-v1",
}


class Output:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.characters = 0
        self.bytes = 0

    def add(self, value: str) -> None:
        self.characters += len(value)
        self.bytes += len(value.encode("utf-8"))
        if self.characters > 100_000 or self.bytes > 200_000:
            raise ValueError("extracted_text_too_large")
        self.parts.append(value)

    def result(self) -> str:
        value = "".join(self.parts).strip()
        if not value:
            raise ValueError("extracted_text_empty")
        return value


def csv_text(value: str) -> str:
    output = Output()
    previous_limit = csv.field_size_limit(6000)
    try:
        rows = csv.reader(io.StringIO(value, newline=""), delimiter=",", quotechar='"', strict=True)
        headers = next(rows, [])
        if not headers or any(not h.strip() for h in headers) or len(set(headers)) != len(headers):
            raise ValueError("csv_header_invalid")
        if len(headers) > 30:
            raise ValueError("csv_limit_exceeded")
        count = 0
        for count, row in enumerate(rows, 1):
            if count > 1000 or len(row) > 30:
                raise ValueError("csv_limit_exceeded")
            if len(row) != len(headers):
                raise ValueError("csv_row_width_invalid")
            # JSON-quoted strings preserve field boundaries, embedded newlines and literal formulas.
            # No type inference, numeric conversion, spreadsheet evaluation or metric import.
            output.add(f"# Record {count}\n")
            for header, cell in zip(headers, row, strict=True):
                output.add(
                    json.dumps(header, ensure_ascii=False)
                    + ": "
                    + json.dumps(cell, ensure_ascii=False)
                    + "\n"
                )
            output.add("\n")
        if not count:
            raise ValueError("extracted_text_empty")
    except csv.Error:
        raise ValueError("invalid_csv") from None
    finally:
        csv.field_size_limit(previous_limit)
    return output.result()


class PassiveHTML(HTMLParser):
    """A conservative subset, NOT a browser renderer or general HTML sanitizer."""

    tags = frozenset(
        [
            "html",
            "head",
            "body",
            "title",
            "meta",
            "p",
            "div",
            "section",
            "article",
            "header",
            "footer",
            "main",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "ul",
            "ol",
            "li",
            "dl",
            "dt",
            "dd",
            "blockquote",
            "pre",
            "code",
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "th",
            "td",
            "span",
            "em",
            "strong",
            "b",
            "i",
            "u",
            "s",
            "small",
            "sub",
            "sup",
            "br",
            "hr",
            "a",
            "img",
            "figure",
            "figcaption",
        ]
    )
    void = frozenset({"br", "hr", "img", "meta"})
    blocks = frozenset(
        [
            "p",
            "div",
            "section",
            "article",
            "header",
            "footer",
            "main",
            "title",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "dt",
            "dd",
            "blockquote",
            "pre",
            "table",
            "tr",
            "figure",
            "figcaption",
        ]
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output = Output()
        self.stack: list[str] = []
        self.events = 0
        self.elements = 0

    def event(self) -> None:
        self.events += 1
        if self.events > 20_000:
            raise ValueError("html_limit_exceeded")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.event()
        self.elements += 1
        if self.elements > 5000 or len(attrs) > 30:
            raise ValueError("html_limit_exceeded")
        if tag not in self.tags:
            raise ValueError("active_document_rejected")
        for name, value in attrs:
            if name.startswith("on") or name in {"style", "srcdoc", "hidden", "http-equiv"}:
                raise ValueError("active_document_rejected")
            if name == "charset" and (value or "").casefold() != "utf-8":
                raise ValueError("text_encoding_invalid")
            if name in {"href", "src"} and value:
                scheme = re.sub(r"[\x00-\x20]", "", value).casefold()
                if scheme.startswith(("javascript:", "vbscript:", "data:", "file:")):
                    raise ValueError("active_document_rejected")
        if tag not in self.void:
            self.stack.append(tag)
            if len(self.stack) > 64:
                raise ValueError("html_limit_exceeded")
        if tag in self.blocks or tag in {"br", "hr"}:
            self.output.add("\n\n")
        elif tag in {"th", "td"}:
            self.output.add("\t")
        # Attributes are never fetched, rendered or treated as visible text.

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        self.event()
        if tag not in self.stack or self.stack[-1] != tag:
            raise ValueError("html_structure_invalid")
        self.stack.pop()
        if tag in self.blocks:
            self.output.add("\n\n")

    def handle_data(self, data: str) -> None:
        self.event()
        self.output.add(data)

    def handle_decl(self, decl: str) -> None:
        self.event()
        if decl.strip().casefold() != "doctype html":
            raise ValueError("active_document_rejected")

    def unknown_decl(self, data: str) -> None:
        raise ValueError("active_document_rejected")

    def handle_pi(self, data: str) -> None:
        raise ValueError("active_document_rejected")

    def handle_comment(self, data: str) -> None:
        self.event()


def extract_text_file(data: bytes, format: str) -> str:
    value = utf8_text(data)
    if format == "csv":
        return csv_text(value)
    if format == "html":
        parser = PassiveHTML()
        parser.feed(value)
        parser.close()
        if parser.stack or not parser.elements:
            raise ValueError("html_structure_invalid")
        result = parser.output.result()
        # Entity expansion must not smuggle control characters into extraction.
        return utf8_text(result.encode("utf-8"))
    if format != "markdown":
        raise ValueError("file_type_mismatch")
    output = Output()
    output.add(value.replace("\r\n", "\n").replace("\r", "\n"))
    return output.result()
