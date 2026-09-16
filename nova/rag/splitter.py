from __future__ import annotations

import re

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:»])\s+|\n\s*\n")


def split_text(text: str, *, max_chars: int = 1500, overlap: int = 150) -> list[str]:
    """Split a document into overlapping chunks for retrieval.

    Recursive strategy: keep whole paragraphs when possible, then sentences,
    then hard character slices. Overlap keeps the retrieval context continuous
    across chunk boundaries.
    """
    text = text.strip()
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            _append(chunks, paragraph, overlap)
            continue
        # paragraph too big: split by sentences
        for sentence in _SENTENCE_SPLIT.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= max_chars:
                _append(chunks, sentence, overlap)
            else:
                # hard slice
                start = 0
                while start < len(sentence):
                    _append(chunks, sentence[start : start + max_chars], overlap)
                    start += max_chars
    return _merge_tiny(chunks, max_chars)


def _append(chunks: list[str], piece: str, overlap: int) -> None:
    if chunks:
        tail = chunks[-1][-overlap:] if overlap else ""
        piece = tail + piece if tail else piece
    chunks.append(piece)


def _merge_tiny(chunks: list[str], max_chars: int) -> list[str]:
    """Glue small fragments back together so we don't produce noise chunks."""
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) + len(chunk) <= max_chars:
            merged[-1] = merged[-1] + " " + chunk
        else:
            merged.append(chunk)
    return merged