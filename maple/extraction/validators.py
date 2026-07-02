"""Validate extracted evidence rows against known paper text."""
from __future__ import annotations

import re


def validate_snippet_in_text(snippet: str, paper_text: str, threshold: float = 0.6) -> bool:
    """Check if the snippet is roughly present in the paper text.

    Uses word-overlap as a proxy for snippet traceability.
    Returns True if enough words from the snippet appear in the paper text.
    """
    if not snippet or not paper_text:
        return False
    snippet_words = set(snippet.lower().split())
    text_words = set(paper_text.lower().split())
    if not snippet_words:
        return False
    overlap = len(snippet_words & text_words) / len(snippet_words)
    return overlap >= threshold


def _normalize_for_match(text: str) -> str:
    """Lowercase and collapse any non-alphanumeric run to a single space."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def validate_quote_fragments(
    snippet: str,
    paper_text: str,
    *,
    min_fragment_words: int = 4,
) -> bool:
    """Verify a snippet is an EXACT quote (searchable) from the paper.

    The snippet may bridge non-contiguous passages with an ellipsis ("..." or "…").
    Each substantial fragment (>= min_fragment_words words) must appear verbatim in
    the paper text, ignoring only punctuation and whitespace differences so the
    quote stays copy-paste findable in the source. Falls back to word-overlap for
    quotes too short to verify as an exact substring.
    """
    if not snippet or not paper_text:
        return False
    norm_text = _normalize_for_match(paper_text)
    fragments = re.split(r"\s*(?:…|\.\.\.+)\s*", snippet)
    verified = 0
    for frag in fragments:
        norm_frag = _normalize_for_match(frag)
        if len(norm_frag.split()) < min_fragment_words:
            continue
        verified += 1
        if norm_frag not in norm_text:
            return False
    if verified:
        return True
    return validate_snippet_in_text(snippet, paper_text, threshold=0.6)


def validate_genes_in_text(genes: list[str], paper_text: str) -> list[str]:
    """Return only genes that appear in the paper text (case-insensitive whole-word check)."""
    if not paper_text:
        return []
    text_upper = paper_text.upper()
    valid: list[str] = []
    for g in genes:
        g_upper = g.upper()
        # Require the gene token to appear as a standalone word (not substring of a longer word).
        # Simple check: surrounded by non-alphanumeric chars or at string boundaries.
        import re
        pattern = r"(?<![A-Z0-9])" + re.escape(g_upper) + r"(?![A-Z0-9])"
        if re.search(pattern, text_upper):
            valid.append(g)
    return valid


def _label_tokens(label: str) -> list[str]:
    """Significant words from a cell-type label for co-occurrence checks."""
    generic = {"cell", "cells", "type", "types", "like", "state", "line", "lines"}
    return [
        w for w in re.findall(r"[a-z0-9+]+", label.lower())
        if len(w) > 2 and w not in generic
    ]


def _token_in_snippet(token: str, snippet_lower: str) -> bool:
    if token in snippet_lower:
        return True
    stem_aliases = {
        "epithelial": ("epithelial", "epithelium"),
        "mesenchymal": ("mesenchymal", "mesenchyme"),
    }
    for alias in stem_aliases.get(token, ()):
        if alias in snippet_lower:
            return True
    return False


def validate_label_in_snippet(celltype_label: str, snippet: str) -> bool:
    """Every distinctive label token must appear in the evidence snippet."""
    tokens = _label_tokens(celltype_label)
    if not tokens:
        return False
    snippet_lower = snippet.lower()
    return all(_token_in_snippet(tok, snippet_lower) for tok in tokens)


def validate_genes_in_snippet(genes: list[str], snippet: str) -> bool:
    """Every gene must appear in the snippet text."""
    if not genes or not snippet:
        return False
    snippet_upper = snippet.upper()
    for g in genes:
        pattern = r"(?<![A-Z0-9])" + re.escape(g.upper()) + r"(?![A-Z0-9])"
        if not re.search(pattern, snippet_upper):
            return False
    return True
