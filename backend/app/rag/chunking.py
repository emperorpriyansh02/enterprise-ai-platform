"""Split a document's text into overlapping passages ("chunks") for embedding and search.

Paragraphs are kept together where possible. A paragraph that's too long is split by sentence,
and a sentence that's too long is split by words. Each chunk starts with the last ~`overlap`
characters of the previous one, so an idea cut at a boundary still appears whole somewhere.
"""

import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    """Chunks of at most `size` characters, in document order."""
    overlap = max(0, min(overlap, size // 2))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces = [piece for paragraph in paragraphs for piece in _fit(paragraph, size)]

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 2 + len(piece) <= size:
            current = f"{current}\n\n{piece}"
        else:
            chunks.append(current)
            tail = _tail(current, overlap)
            current = f"{tail} {piece}" if tail and len(tail) + 1 + len(piece) <= size else piece
    if current:
        chunks.append(current)
    return chunks


def _fit(paragraph: str, size: int) -> list[str]:
    """One paragraph as pieces of at most `size` characters: whole, by sentences, or by words."""
    if len(paragraph) <= size:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        for part in [sentence] if len(sentence) <= size else _split_words(sentence, size):
            if not current:
                current = part
            elif len(current) + 1 + len(part) <= size:
                current = f"{current} {part}"
            else:
                pieces.append(current)
                current = part
    if current:
        pieces.append(current)
    return pieces


def _split_words(text: str, size: int) -> list[str]:
    """Pack words into pieces of at most `size`; a single word longer than `size` is cut."""
    pieces: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > size:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:size])
            word = word[size:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= size:
            current = f"{current} {word}"
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


def _tail(text: str, overlap: int) -> str:
    """The last `overlap` characters of `text`, starting at a word boundary."""
    if overlap == 0 or len(text) <= overlap:
        return "" if overlap == 0 else text
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if space != -1 else tail
