"""Tiny synthetic in-memory documents. No employee files or real malware."""

import io
import zipfile
from xml.sax.saxutils import escape

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def docx(
    value: str = "Крем ALPHA-42. Бережное очищение.", extra: dict[str, bytes] | None = None
) -> bytes:
    buffer = io.BytesIO()
    parts = {
        "[Content_Types].xml": (
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
            b'officedocument.wordprocessingml.document.main+xml"/></Types>'
        ),
        "word/document.xml": (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + escape(value)
            + "</w:t></w:r></w:p></w:body></w:document>"
        ).encode(),
    }
    parts.update(extra or {})
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def pdf(*, blank: bool = False, active: bool = False, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    if not blank:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 200 Td (Synthetic ALPHA-42 cream) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if active:
        writer.add_js("app.alert('synthetic');")
    if encrypted:
        writer.encrypt("test-only")
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()
