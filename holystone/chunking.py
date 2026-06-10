"""Chunking shared by the condense and embed stages.

Scene breaks (horizontal rules) are the preferred boundaries; oversized
pieces sub-split at turn markers or paragraph boundaries, accumulating
up to max_chars. Deterministic, order-preserving.
"""

from __future__ import annotations

import re

HRULE_SPLIT = re.compile(r"\n\s*(?:-{3,}|_{3,}|\*{3,})\s*\n")
TURN_MARK = re.compile(r"^\[(?:PLAYER|NARRATOR)\]\s*$")


def _split_oversized(piece: str, max_chars: int) -> list[str]:
    paragraphs = piece.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for para in paragraphs:
        para_len = len(para) + 2
        is_turn_boundary = bool(TURN_MARK.match(para.strip().splitlines()[0])) if para.strip() else False

        if current and size + para_len > max_chars and (is_turn_boundary or size > max_chars * 0.5):
            chunks.append("\n\n".join(current).strip())
            current, size = [], 0

        current.append(para)
        size += para_len

    if current:
        chunks.append("\n\n".join(current).strip())
    return [c for c in chunks if c]


def split_chunks(text: str, max_chars: int = 6000) -> list[str]:
    chunks: list[str] = []
    for piece in HRULE_SPLIT.split(text):
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) <= max_chars:
            chunks.append(piece)
        else:
            chunks.extend(_split_oversized(piece, max_chars))
    return chunks
