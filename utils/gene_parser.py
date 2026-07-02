"""Robust marker gene parsing utilities."""

from __future__ import annotations

import re

_GENE_SPLIT_PATTERN = re.compile(r"[\s,]+")


def parse_genes(raw_input: str) -> tuple[list[str], list[str]]:
    """
    Parse comma-, newline-, or space-separated gene symbols.

    Returns:
        Tuple of (deduplicated uppercase gene list preserving order, warnings).
    """
    warnings: list[str] = []
    if not raw_input or not raw_input.strip():
        return [], ["No marker genes provided."]

    tokens = _GENE_SPLIT_PATTERN.split(raw_input.strip())
    seen: set[str] = set()
    genes: list[str] = []

    for token in tokens:
        gene = token.strip().upper()
        if not gene:
            continue
        if gene not in seen:
            seen.add(gene)
            genes.append(gene)

    if len(genes) < 3:
        warnings.append(
            f"Only {len(genes)} gene(s) provided. At least 3 markers are recommended "
            "for reliable cell-type annotation."
        )
    if len(genes) > 100:
        warnings.append(
            f"{len(genes)} genes provided. For MVP performance, please limit to 100 genes."
        )

    return genes, warnings
