"""Pick the parts of a full-text paper that are worth reading for these genes.

Instead of sending the first N characters (mostly introduction and methods), the
paper is cut into chunks of a few sentences, each chunk is scored by how many of
the user's genes it mentions and whether it reads like marker / cluster language
or a figure legend, and the best chunks are sent in their original order.

Chunks are copied verbatim, so quotes taken from them are still exact quotes from
the paper. Non-adjacent chunks are separated by "..." which matches how the
extraction prompt joins quote fragments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from maples.genes import genes_in_text

SEPARATOR = "\n...\n"
CHUNK_CHARS = 700

_CUES = (
    "marker", "markers", "cluster", "clusters", "subcluster", "population", "populations",
    "subpopulation", "subset", "subsets", "express", "expressed", "expressing", "expression",
    "enriched", "identified", "defined", "annotated", "positive", "negative", "cells",
    "cell type", "cell state", "signature", "transitional", "lineage",
)
_LEGEND = re.compile(r"\b(?:Fig(?:ure)?\.?\s*S?\d+|Supplementary (?:Fig|Table)|Table\s*S?\d+)\b", re.I)
_REF_HEADING = re.compile(r"\b(?:References|REFERENCES|Literature Cited|Bibliography)\b")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


@dataclass
class PassageStats:
    original_chars: int
    selected_chars: int
    chunks_total: int
    chunks_with_genes: int
    chunks_selected: int


def strip_references(text: str) -> str:
    """Drop the reference list: the last 'References' heading in the final 40% of the text."""
    cut_from = int(len(text) * 0.6)
    last = None
    for match in _REF_HEADING.finditer(text, cut_from):
        last = match
    return text[: last.start()] if last else text


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[tuple[int, str]]:
    """Consecutive sentences grouped into chunks of about ``size`` characters.
    Returns (start offset, chunk) pairs; chunks are exact substrings of ``text``."""
    chunks: list[tuple[int, str]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.start()
        if end - start >= size:
            chunks.append((start, text[start:end]))
            start = match.end()
    if start < len(text):
        tail = text[start:]
        # split very long unpunctuated tails so one blob cannot eat the budget
        while len(tail) > size * 2:
            chunks.append((start, tail[:size * 2]))
            start += size * 2
            tail = tail[size * 2:]
        if tail.strip():
            chunks.append((start, tail))
    return chunks


def _score(chunk: str, genes: list[str]) -> tuple[int, list[str]]:
    found = genes_in_text(genes, chunk)
    if not found:
        return 0, found
    lower = chunk.lower()
    cues = sum(1 for c in _CUES if c in lower)
    legend = 2 if _LEGEND.search(chunk) else 0
    return 5 * len(found) + min(cues, 5) + legend, found


def select_passages(text: str, genes: list[str], max_chars: int) -> tuple[str, PassageStats]:
    """Best gene-bearing chunks of ``text`` (in original order) within ``max_chars``.

    Falls back to the start of the text when no chunk mentions any gene.
    """
    if not text:
        return "", PassageStats(0, 0, 0, 0, 0)
    body = strip_references(text)
    chunks = chunk_text(body)
    scored = []
    for idx, (start, chunk) in enumerate(chunks):
        score, found = _score(chunk, genes)
        if score:
            scored.append((score, idx, start, chunk, found))

    if not scored:
        selected = body[:max_chars]
        return selected, PassageStats(len(text), len(selected), len(chunks), 0, 0)

    # Greedy by score, but make sure every gene gets at least one chunk if possible.
    chosen: dict[int, str] = {}
    budget = max_chars
    covered: set[str] = set()
    by_score = sorted(scored, key=lambda s: (-s[0], s[1]))
    for pass_no in (0, 1):
        for score, idx, start, chunk, found in by_score:
            if idx in chosen:
                continue
            if pass_no == 0 and set(found) <= covered:
                continue  # first pass: only chunks that add a new gene
            cost = len(chunk) + len(SEPARATOR)
            if cost > budget:
                continue
            chosen[idx] = chunk
            budget -= cost
            covered.update(found)

    ordered = sorted(chosen)
    parts: list[str] = []
    prev = None
    for idx in ordered:
        if prev is not None and idx != prev + 1:
            parts.append(SEPARATOR)
        elif prev is not None:
            parts.append(" ")
        parts.append(chosen[idx])
        prev = idx
    selected = "".join(parts)
    return selected, PassageStats(len(text), len(selected), len(chunks), len(scored), len(chosen))
