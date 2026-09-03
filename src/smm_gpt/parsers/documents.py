"""In-memory, bounded PDF/DOCX extraction. Production callers MUST use the sandbox."""

import io
import logging
import stat
import zipfile
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring
from pypdf import PdfReader

from smm_gpt.parsers.text_files import TEXT_PARSER_VERSIONS, extract_text_file

MAX_INPUT = 2 * 1024 * 1024
MAX_OUTPUT = 200_000
PARSER_VERSION = "pdf-pypdf-6.16.2_docx-ooxml-v1"


def parser_version(format: str) -> str:
    return TEXT_PARSER_VERSIONS.get(format, PARSER_VERSION)


def bounded(value: str) -> str:
    if len(value) > 100_000 or len(value.encode("utf-8")) > MAX_OUTPUT:
        raise ValueError("extracted_text_too_large")
    return value


def xml(data: bytes) -> Element:
    result: Element = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    return result


def docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 200 or len({i.filename for i in entries}) != len(entries):
            raise ValueError("docx_archive_invalid")
        expanded = 0
        for entry in entries:
            name = entry.filename
            if (
                name.startswith("/")
                or "\\" in name
                or ":" in name
                or ".." in name.split("/")
                or stat.S_ISLNK(entry.external_attr >> 16)
                or entry.flag_bits & 1
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            ):
                raise ValueError("docx_archive_invalid")
            expanded += entry.file_size
            if (
                expanded > 8 * 1024 * 1024
                or entry.file_size > 2 * 1024 * 1024
                or entry.file_size > max(1, entry.compress_size) * 100
            ):
                raise ValueError("docx_expansion_limit")
            if any(
                x in name.casefold() for x in ("vbaproject", "embeddings/", "activex/", "customui/")
            ):
                raise ValueError("active_document_rejected")
        names = {i.filename for i in entries}
        if not {"[Content_Types].xml", "word/document.xml"} <= names:
            raise ValueError("docx_archive_invalid")
        # No extraction to disk. Parse every relationship and XML part safely; no fetches.
        parts: dict[str, Element] = {}
        for entry in entries:
            if entry.filename.endswith((".xml", ".rels")):
                root = xml(archive.read(entry))
                parts[entry.filename] = root
                for element in root.iter():
                    if element.attrib.get("TargetMode") == "External" or any(
                        x in str(value).casefold()
                        for value in element.attrib.values()
                        for x in ("macroenabled", "oleobject", "attachedtemplate", "afchunk")
                    ):
                        raise ValueError("active_document_rejected")
        root = parts["word/document.xml"]
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        if (
            not any(
                node.attrib.get("PartName") == "/word/document.xml"
                and node.attrib.get("ContentType")
                == (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document.main+xml"
                )
                for node in parts["[Content_Types].xml"].iter()
            )
            or root.tag != ns + "document"
        ):
            raise ValueError("docx_archive_invalid")
        if any(
            node.tag in {ns + "instrText", ns + "fldSimple", ns + "altChunk", ns + "object"}
            for part in parts.values()
            for node in part.iter()
        ):
            raise ValueError("active_document_rejected")
        output = []
        for paragraph in root.iter(ns + "p"):
            line = "".join(node.text or "" for node in paragraph.iter(ns + "t"))
            output.append(line)
            bounded("\n\n".join(output))
        return bounded("\n\n".join(output))


def pdf(data: bytes) -> str:
    logging.getLogger("pypdf").disabled = True
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or len(reader.pages) > 50:
        raise ValueError("encrypted_or_oversized_pdf")
    # Conservatively reject interactive/embedded payloads, including nested actions.
    forbidden = {
        "/JS",
        "/JavaScript",
        "/OpenAction",
        "/AA",
        "/Launch",
        "/EmbeddedFiles",
        "/EmbeddedFile",
        "/AcroForm",
        "/RichMedia",
        "/XFA",
        "/SubmitForm",
        "/GoToR",
        "/GoToE",
        "/Rendition",
        "/ImportData",
        "/Sound",
        "/Movie",
    }
    seen: set[int] = set()
    todo: list[object] = [reader.root_object]
    while todo:
        value = todo.pop()
        if hasattr(value, "get_object"):
            value = value.get_object()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if len(seen) > 20_000:
            raise ValueError("pdf_object_limit")
        if isinstance(value, dict):
            if forbidden & set(value) or str(value.get("/S")) in forbidden:
                raise ValueError("active_document_rejected")
            todo.extend(value.values())
        elif isinstance(value, list):
            todo.extend(value)
    output = []
    for number, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        if contents and len(contents.get_data()) > 4 * 1024 * 1024:
            raise ValueError("pdf_stream_limit")
        output.append(f"# Page {number}\n\n" + page.extract_text())
        bounded("\n\n".join(output))
    # Don't treat page headings as extracted evidence for scanned/image-only PDFs.
    if not any(
        line.strip() and not line.startswith("# Page ")
        for part in output
        for line in part.splitlines()
    ):
        raise ValueError("ocr_required")
    return bounded("\n\n".join(output))


def extract(data: bytes, format: str) -> str:
    if not 0 < len(data) <= MAX_INPUT:
        raise ValueError("file_size_invalid")
    if format == "pdf" and data.startswith(b"%PDF-"):
        result = pdf(data)
    elif format == "docx" and data.startswith(b"PK\x03\x04"):
        result = docx(data)
    elif format in TEXT_PARSER_VERSIONS:
        result = extract_text_file(data, format)
    else:
        raise ValueError("file_type_mismatch")
    if not result.strip():
        raise ValueError("extracted_text_empty")
    return result
