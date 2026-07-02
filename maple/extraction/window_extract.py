"""Sentence-window marker attribution for literature evidence extraction."""
from __future__ import annotations

import re


def markers_in_text(genes_upper: list[str], text_upper: str) -> list[str]:
    """Return input markers mentioned as whole-word tokens in the text."""
    return [g for g in genes_upper if re.search(rf"\b{re.escape(g)}\b", text_upper)]


def cell_type_specific_genes(
    genes_upper: list[str],
    text: str,
    cell_type_phrases: list[str],
    window: int = 2,
) -> list[str]:
    """
    Return genes from the single best anchor window around a cell-type mention.

    For each sentence that names the cell type, a ±window-sentence context is
    checked. Genes from whichever anchor captures the most input genes are
    returned (not the union of all anchors).
    """
    if not cell_type_phrases or not text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    n = len(sentences)
    best_genes: list[str] = []

    for i, sent in enumerate(sentences):
        if not any(phrase in sent.lower() for phrase in cell_type_phrases):
            continue
        idxs = range(max(0, i - window), min(n, i + window + 1))
        window_text = " ".join(sentences[j] for j in idxs).upper()
        genes_here = [g for g in genes_upper if re.search(rf"\b{re.escape(g)}\b", window_text)]
        if len(genes_here) > len(best_genes):
            best_genes = genes_here

    return best_genes


def cell_type_union_genes(
    genes_upper: list[str],
    text: str,
    cell_type_phrases: list[str],
    window: int = 2,
) -> list[str]:
    """
    Union of input genes found in any anchor window around a cell-type mention.

    Used when marker panels are split across adjacent sentences (IPF aberrant
    basaloid cells in Habermann et al. and related papers).
    """
    if not cell_type_phrases or not text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    n = len(sentences)
    found: set[str] = set()

    for i, sent in enumerate(sentences):
        if not any(phrase in sent.lower() for phrase in cell_type_phrases):
            continue
        idxs = range(max(0, i - window), min(n, i + window + 1))
        window_text = " ".join(sentences[j] for j in idxs).upper()
        for g in genes_upper:
            if re.search(rf"\b{re.escape(g)}\b", window_text):
                found.add(g)

    return [g for g in genes_upper if g in found]
