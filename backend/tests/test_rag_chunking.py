import itertools

from app.rag.chunking import split_text


def test_short_text_is_one_chunk():
    assert split_text("One paragraph.\n\nAnother one.", size=200, overlap=20) == [
        "One paragraph.\n\nAnother one."
    ]


def test_chunks_respect_size_and_keep_paragraphs_together():
    paragraphs = [f"Paragraph {i} " + "word " * 30 for i in range(10)]
    chunks = split_text("\n\n".join(paragraphs), size=400, overlap=0)
    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)
    # Without overlap, every paragraph appears whole in exactly one chunk.
    for paragraph in paragraphs:
        assert sum(paragraph.strip() in c for c in chunks) == 1


def test_overlap_repeats_the_end_of_the_previous_chunk():
    text = "\n\n".join(f"Sentence number {i} is here." for i in range(40))
    chunks = split_text(text, size=200, overlap=60)
    for previous, current in itertools.pairwise(chunks):
        assert len(current) <= 200
        first_words = " ".join(current.split()[:3])
        assert first_words in previous  # the chunk starts with text from the end of the previous one


def test_long_paragraph_splits_by_sentence_then_words():
    sentences = " ".join(f"This is sentence {i} of a very long paragraph." for i in range(30))
    giant_word = "x" * 450
    chunks = split_text(f"{sentences}\n\n{giant_word}", size=200, overlap=0)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(c for c in chunks if set(c) == {"x"}) == giant_word  # cut, but nothing lost


def test_overlap_is_capped_at_half_the_size():
    chunks = split_text("\n\n".join(["a " * 90] * 4), size=200, overlap=5000)
    assert all(len(c) <= 200 for c in chunks)


def test_empty_text_gives_no_chunks():
    assert split_text("   \n\n  ") == []
