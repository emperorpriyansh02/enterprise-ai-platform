"""Turn an uploaded file into plain text: .txt, .md, .pdf, .docx, .html.

These run on untrusted files, so they only read text (no macros, scripts or links are followed),
and the caller runs them in a worker thread so a slow file can't block the server.
"""

import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePath

from app.core.errors import DocumentParseError, UnsupportedFileTypeError

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
BY_EXTENSION = {
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": DOCX,
    ".html": "text/html",
    ".htm": "text/html",
}
SUPPORTED_TYPES = sorted(set(BY_EXTENSION.values()))
MAX_TEXT_CHARS = 5_000_000  # ~1,000 pages; protects the database and the embedder


@dataclass(frozen=True)
class ExtractedDocument:
    title: str
    text: str
    mime_type: str


def detect_type(filename: str | None, content_type: str | None) -> str:
    """The file's type, from its extension first, then from the browser's content type."""
    extension = PurePath(filename or "").suffix.lower()
    if extension in BY_EXTENSION:
        return BY_EXTENSION[extension]
    base_type = (content_type or "").split(";")[0].strip().lower()
    if base_type in SUPPORTED_TYPES:
        return base_type
    raise UnsupportedFileTypeError(
        f'"{filename or "file"}" isn\'t a supported type. Use .txt, .md, .pdf, .docx or .html.'
    )


def extract_text(filename: str | None, content_type: str | None, data: bytes) -> ExtractedDocument:
    """Read `data` and give back its title and clean plain text."""
    mime_type = detect_type(filename, content_type)
    reader = {
        "text/plain": _from_text,
        "text/markdown": _from_markdown,
        "application/pdf": _from_pdf,
        DOCX: _from_docx,
        "text/html": _from_html,
    }[mime_type]
    title, text = reader(data)
    text = normalize_text(text)
    if not text:
        hint = (
            " It may be a scanned image; OCR isn't supported yet." if mime_type == "application/pdf" else ""
        )
        raise DocumentParseError(f'"{filename or "file"}" has no readable text.{hint}')
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentParseError(
            f'"{filename or "file"}" has more than {MAX_TEXT_CHARS:,} characters of text.'
        )
    fallback = PurePath(filename).stem if filename else "Untitled"
    return ExtractedDocument(title=(title or fallback).strip()[:300], text=text, mime_type=mime_type)


def normalize_text(text: str) -> str:
    """Unix line ends, no NUL characters (Postgres rejects them), no trailing spaces, max one blank line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")  # never fails; good enough for old Windows text files


def _from_text(data: bytes) -> tuple[str | None, str]:
    return None, _decode(data)


def _from_markdown(data: bytes) -> tuple[str | None, str]:
    text = _decode(data)
    heading = re.search(r"^#\s+(.+)$", text, flags=re.M)
    return (heading.group(1).strip() if heading else None), text


def _from_pdf(data: bytes) -> tuple[str | None, str]:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentParseError("This PDF is password-protected.")
        pages = [page.extract_text() or "" for page in reader.pages]
        title = reader.metadata.title if reader.metadata else None
    except DocumentParseError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError) as exc:
        raise DocumentParseError(f"Couldn't read this PDF: {exc}") from exc
    return title, "\n\n".join(pages)


def _from_docx(data: bytes) -> tuple[str | None, str]:
    import zipfile

    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise DocumentParseError(f"Couldn't read this Word file: {exc}") from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return document.core_properties.title or None, "\n\n".join(parts)


def _from_html(data: bytes) -> tuple[str | None, str]:
    parser = _HTMLText()
    parser.feed(_decode(data))
    parser.close()
    return parser.title, parser.text()


class _HTMLText(HTMLParser):
    """Collects visible text; skips script/style; puts block elements on their own lines."""

    SKIP = frozenset({"script", "style", "noscript", "template", "svg"})
    BLOCKS = frozenset(
        {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCKS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCKS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title = (self.title or "") + data.strip()
            return
        self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in joined.split("\n"))
