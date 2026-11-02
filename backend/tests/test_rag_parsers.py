import io

import docx
import pytest

from app.core.errors import DocumentParseError, UnsupportedFileTypeError
from app.rag.parsers import DOCX, detect_type, extract_text, normalize_text


def make_pdf(text: str | None) -> bytes:
    """A minimal one-page PDF; `None` makes a page with no text (like a scanned image)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def make_docx() -> bytes:
    document = docx.Document()
    document.core_properties.title = "Leave Policy"
    document.add_paragraph("Employees get 25 days of annual leave.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Sick leave"
    table.rows[0].cells[1].text = "10 days"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_detect_type_by_extension_then_content_type():
    assert detect_type("notes.MD", None) == "text/markdown"
    assert detect_type("blob", "application/pdf; charset=binary") == "application/pdf"
    with pytest.raises(UnsupportedFileTypeError):
        detect_type("photo.png", "image/png")


def test_text_and_markdown_title():
    plain = extract_text("readme.txt", None, b"Hello\r\nworld\x00!")
    markdown = extract_text("guide.md", None, b"intro\n\n# Getting Started\n\nSteps...")
    assert (plain.title, plain.text) == ("readme", "Hello\nworld!")  # NUL removed, Windows line ends fixed
    assert markdown.title == "Getting Started" and markdown.mime_type == "text/markdown"


def test_latin1_text_falls_back_cleanly():
    assert extract_text("old.txt", None, "Café menu".encode("latin-1")).text == "Café menu"


def test_html_skips_scripts_and_uses_title():
    html = b"""<html><head><title>Pricing</title><style>p{color:red}</style></head>
    <body><h1>Plans</h1><p>Pro costs &pound;20.</p><script>alert('x')</script></body></html>"""
    result = extract_text("page.html", None, html)
    assert result.title == "Pricing"
    assert "Plans" in result.text and "Pro costs £20." in result.text
    assert "alert" not in result.text and "color" not in result.text


def test_docx_paragraphs_tables_and_title():
    result = extract_text("policy.docx", None, make_docx())
    assert result.title == "Leave Policy" and result.mime_type == DOCX
    assert "25 days of annual leave" in result.text
    assert "Sick leave | 10 days" in result.text


def test_pdf_text():
    result = extract_text("manual.pdf", None, make_pdf("Reset the router by holding the button."))
    assert "Reset the router" in result.text and result.title == "manual"


def test_pdf_without_text_explains_scans():
    with pytest.raises(DocumentParseError, match="scanned"):
        extract_text("scan.pdf", None, make_pdf(None))


def test_broken_files_give_parse_errors():
    with pytest.raises(DocumentParseError):
        extract_text("broken.pdf", None, b"not a pdf")
    with pytest.raises(DocumentParseError):
        extract_text("broken.docx", None, b"not a zip")


def test_empty_text_rejected():
    with pytest.raises(DocumentParseError, match="no readable text"):
        extract_text("blank.txt", None, b"  \n\n ")


def test_normalize_collapses_blank_lines():
    assert normalize_text("a  \n\n\n\nb\r\n") == "a\n\nb"
