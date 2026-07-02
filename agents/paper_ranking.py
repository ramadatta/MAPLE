"""Rank retrieved PubMed papers by composite cell-type attribution quality.

One row is emitted per (paper × cell-type) combination, so a paper that describes
two distinct populations (e.g. Aberrant Basaloid AND AT2) will appear twice in the
ranked list — once for each population with its own attributed gene set.

Four scoring criteria (weighted):

  Specificity (60%): genes attributed to this cell type / total input genes.
  Coverage    (25%): all matched input genes / total input genes.
  Context     (10%): single-cell / scRNA-seq papers score highest.
  Precision    (5%): recency bonus (newer publications preferred).

Attribution uses sentence-window co-mention: for each sentence that names the
cell type, a ±2-sentence window is searched for input genes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

from models.schemas import PaperCoverage, PubMedPaper
from utils.cell_type_lexicon import (
    best_display_name,
    get_cell_type_phrases,
    infer_all_cell_types_from_literature,
    is_generic_cell_type,
    load_canonical_cell_types,
)

TextProvider = Callable[[PubMedPaper], str]

_SCRNA_KEYWORDS = frozenset({
    "single-cell", "scrna", "single cell", "scrna-seq",
    "10x genomics", "dropseq", "smart-seq", "snrna-seq",
})


def _markers_in_text(genes_upper: list[str], text_upper: str) -> list[str]:
    """Return input markers mentioned as whole-word tokens in the text."""
    return [g for g in genes_upper if re.search(rf"\b{re.escape(g)}\b", text_upper)]


def _cell_type_specific_genes(
    genes_upper: list[str],
    text: str,
    cell_type_phrases: list[str],
    window: int = 2,
) -> list[str]:
    """
    Return genes from the SINGLE BEST anchor window around a cell-type mention.

    For each sentence that names the cell type (an "anchor"), a ±window-sentence
    context is checked.  We return genes from whichever anchor captures the most
    input genes — NOT the union of all anchors.

    Using the best single window prevents generic terms like "fibroblasts" (which
    appear throughout an IPF full-text paper) from accumulating genes across
    dozens of scattered mentions, while still giving specific terms like
    "aberrant basaloid" full credit when they co-occur with many genes in one passage.

    ``window`` should be smaller for full-text papers (1) and larger for
    abstract-only papers (2) to balance precision and recall.
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


def _paper_composite_score(
    total_input: int,
    specific_count: int,
    all_matched: int,
    year: int | None,
    text_lower: str,
    cell_type: str = "",
) -> float:
    """
    4-criterion composite score.

    Specificity (60%): fraction of input genes attributed to this cell type.
        Generic cell types (Fibroblast, Macrophage, T cell…) receive a 0.4×
        penalty because they appear throughout virtually every paper in a tissue
        context and would otherwise dominate over specific populations.
    Coverage    (25%): fraction of input genes found anywhere in the paper.
    Context     (10%): single-cell / scRNA-seq evidence is highest quality.
    Precision    (5%): recency bonus (year 2000→0.0, 2025→1.0).
    """
    raw_specificity = specific_count / max(1, total_input)
    # Penalise generic lineage labels so specific populations (Aberrant Basaloid,
    # Kupffer cell, Podocyte…) naturally rank above broad terms.
    specificity_factor = 0.4 if is_generic_cell_type(cell_type) else 1.0
    specificity = raw_specificity * specificity_factor

    coverage = all_matched / max(1, total_input)
    is_scrna = any(kw in text_lower for kw in _SCRNA_KEYWORDS)
    context = 1.0 if is_scrna else 0.5
    precision = min(1.0, max(0.0, ((year or 2010) - 2000) / 25.0))
    return 0.60 * specificity + 0.25 * coverage + 0.10 * context + 0.05 * precision


def _representative_sentence(markers: list[str], phrase: str | None, paper: PubMedPaper) -> str:
    """Sentence that best evidences the call (cell-type phrase preferred)."""
    if not paper.abstract:
        return paper.title.strip()
    sentences = re.split(r"(?<=[.!?])\s+", paper.abstract)
    if phrase:
        for s in sentences:
            if phrase.lower() in s.lower():
                return s.strip()[:300]
    for s in sentences:
        up = s.upper()
        if any(m in up for m in markers):
            return s.strip()[:300]
    return sentences[0].strip()[:300]


@dataclass
class _ScoredRow:
    coverage: PaperCoverage
    composite: float
    specific_count: int
    marker_count: int
    year: int


def rank_papers_by_marker_coverage(
    genes: list[str],
    papers_by_gene: dict[str, list[PubMedPaper]],
    canonical_types: list[str] | None = None,
    text_fn: Optional[TextProvider] = None,
    max_full_text: int = 60,
) -> list[PaperCoverage]:
    """
    Deduplicate papers and emit one ranked row per (paper × cell-type) combination.

    A paper that describes two distinct populations will produce two rows, each
    with its own attributed gene list and composite score. Rows are sorted globally
    by composite score so the most specific, best-supported associations appear first.

    When ``text_fn`` is provided, gene counting uses open-access full text first,
    falling back to title + abstract when full text is unavailable.
    ``max_full_text`` caps fetch count to bound runtime.
    """
    genes_upper = [g.upper() for g in genes]
    total_input = max(1, len(genes_upper))
    canonical = canonical_types or load_canonical_cell_types()

    seen: dict[str, PubMedPaper] = {}
    for papers in papers_by_gene.values():
        for paper in papers:
            if paper.pmid and paper.pmid not in seen:
                seen[paper.pmid] = paper

    # Open-access papers first so full-text budget is used on indexable papers.
    ordered = sorted(seen.values(), key=lambda p: bool(getattr(p, "pmcid", "")), reverse=True)

    scored_rows: list[_ScoredRow] = []
    full_text_fetches = 0

    for paper in ordered:
        base_text = f"{paper.title}. {paper.abstract}"

        full_text = ""
        if text_fn is not None and full_text_fetches < max_full_text:
            try:
                full_text = text_fn(paper) or ""
            except Exception as exc:
                logger.warning("Full-text fetch failed for PMID %s: %s", paper.pmid, exc)
            full_text_fetches += 1

        used_full_text = bool(full_text)
        search_text = f"{base_text} {full_text}" if used_full_text else base_text
        matched = _markers_in_text(genes_upper, search_text.upper())
        if not matched:
            continue

        text_lower = search_text.lower()

        # Tighter window for full text (many mentions of generic terms like
        # "fibroblasts" would otherwise accumulate genes across the whole paper).
        attr_window = 1 if used_full_text else 2

        # Detect ALL cell types mentioned in this paper.
        ct_pairs = infer_all_cell_types_from_literature(search_text, canonical)

        if ct_pairs:
            for cell_type, phrase in ct_pairs:
                ct_phrases = get_cell_type_phrases(cell_type)
                specific_genes = _cell_type_specific_genes(
                    genes_upper, search_text, ct_phrases, window=attr_window
                )
                if not specific_genes:
                    # Cell type mentioned but no input genes in best anchor window.
                    continue

                display_type = best_display_name(cell_type, phrase)
                score = _paper_composite_score(
                    total_input=total_input,
                    specific_count=len(specific_genes),
                    all_matched=len(matched),
                    year=paper.year,
                    text_lower=text_lower,
                    cell_type=cell_type,
                )
                logger.debug(
                    "PMID %s | %r | specific=%d | matched=%d | score=%.3f",
                    paper.pmid, display_type, len(specific_genes), len(matched), score,
                )
                row = PaperCoverage(
                    pmid=paper.pmid,
                    title=paper.title,
                    journal=paper.journal,
                    year=paper.year,
                    pubmed_url=paper.pubmed_url or f"https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}/",
                    cell_type=display_type,
                    matched_markers=matched,
                    marker_count=len(matched),
                    cell_type_specific_genes=specific_genes,
                    composite_score=round(score, 4),
                    evidence_sentence=_representative_sentence(matched, phrase, paper),
                    full_text_used=used_full_text,
                )
                scored_rows.append(
                    _ScoredRow(
                        coverage=row,
                        composite=score,
                        specific_count=len(specific_genes),
                        marker_count=len(matched),
                        year=paper.year or 0,
                    )
                )
        else:
            # No named cell type found — emit one row with all matched genes.
            score = _paper_composite_score(
                total_input=total_input,
                specific_count=0,
                all_matched=len(matched),
                year=paper.year,
                text_lower=text_lower,
                cell_type="",
            )
            row = PaperCoverage(
                pmid=paper.pmid,
                title=paper.title,
                journal=paper.journal,
                year=paper.year,
                pubmed_url=paper.pubmed_url or f"https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}/",
                cell_type="",
                matched_markers=matched,
                marker_count=len(matched),
                cell_type_specific_genes=[],
                composite_score=round(score, 4),
                evidence_sentence=_representative_sentence(matched, None, paper),
                full_text_used=used_full_text,
            )
            scored_rows.append(
                _ScoredRow(
                    coverage=row,
                    composite=score,
                    specific_count=0,
                    marker_count=len(matched),
                    year=paper.year or 0,
                )
            )

    # Global sort: composite (desc) → specific count (desc) → total matched (desc) → year (desc)
    scored_rows.sort(
        key=lambda x: (x.composite, x.specific_count, x.marker_count, x.year),
        reverse=True,
    )
    return [x.coverage for x in scored_rows]


def final_cell_type_from_ranked(ranked: list[PaperCoverage]) -> tuple[str, PaperCoverage | None]:
    """Top paper with a named cell type drives the call; returns (cell_type, paper)."""
    for paper in ranked:
        if paper.cell_type and paper.cell_type_specific_genes:
            return paper.cell_type, paper
    for paper in ranked:
        if paper.cell_type:
            return paper.cell_type, paper
    return "", None
