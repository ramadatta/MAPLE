"""Robust marker gene parsing utilities.

Accepts the messy variety of formats people actually paste:
- comma separated:      COL1A1, COL3A1, POSTN
- whitespace / one-per-line
- tab separated (e.g. copied from a spreadsheet row)
- semicolon / pipe separated
- Python / R list syntax:  ["COL1A1", "COL3A1"]  or  c('COL1A1','COL3A1')

Surrounding brackets/quotes are stripped, purely numeric tokens (p-values,
log-fold-changes that sneak in from a pasted table) are dropped, and symbols
are upper-cased and de-duplicated while preserving order.
"""

from __future__ import annotations

import re

# Split on commas, whitespace (incl. tabs/newlines), semicolons and pipes.
_GENE_SPLIT_PATTERN = re.compile(r"[\s,;|]+")

# Characters that wrap tokens in list/quote syntax — stripped from each end.
_WRAP_CHARS = "[](){}\"'`"

# A token must contain at least one letter and only gene-symbol-ish characters.
_VALID_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$")

# Pure numbers / floats / scientific notation (e.g. "0.001", "-2.5", "1.2e-3").
_NUMERIC = re.compile(r"^[+-]?\d*\.?\d+([eE][+-]?\d+)?$")


def _clean_token(token: str) -> str:
    """Strip list/quote wrappers and trailing punctuation, then upper-case."""
    t = token.strip().strip(_WRAP_CHARS).strip(",.;:").strip(_WRAP_CHARS)
    return t.upper()


def parse_genes(raw_input: str) -> tuple[list[str], list[str]]:
    """
    Parse gene symbols from a free-form string in any common delimiter/format.

    Returns:
        Tuple of (deduplicated uppercase gene list preserving order, warnings).
    """
    warnings: list[str] = []
    if not raw_input or not raw_input.strip():
        return [], ["No marker genes provided."]

    # Normalise a leading R-style `c(` vector wrapper to a bare paren so the
    # first symbol isn't fused to the "c" (e.g. c('A','B') -> ('A','B')).
    text = re.sub(r"^\s*c\s*\(", "(", raw_input.strip(), flags=re.IGNORECASE)

    tokens = _GENE_SPLIT_PATTERN.split(text)
    seen: set[str] = set()
    genes: list[str] = []

    for token in tokens:
        gene = _clean_token(token)
        if not gene:
            continue
        # Drop numeric-only tokens (stray values from a pasted table).
        if _NUMERIC.match(gene):
            continue
        # Must contain a letter and look like a symbol.
        if not any(c.isalpha() for c in gene):
            continue
        if not _VALID_SYMBOL.match(gene):
            continue
        if gene not in seen:
            seen.add(gene)
            genes.append(gene)

    if 0 < len(genes) < 3:
        warnings.append(
            f"Only {len(genes)} gene(s) provided. At least 3 markers are recommended "
            "for reliable cell-type annotation."
        )
    if len(genes) > 200:
        warnings.append(
            f"{len(genes)} genes provided; consider trimming to your top ~100 markers "
            "for faster, more focused results."
        )

    return genes, warnings
