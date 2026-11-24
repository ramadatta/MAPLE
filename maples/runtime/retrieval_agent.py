"""Retrieval Agent — wraps PubMed + PMC services to produce RetrievalResult."""
from __future__ import annotations

import logging

from maples import config as cfg
from maples.models import AnalysisInput, RetrievalResult, RetrievedPaper
from maples.extraction.evidence_signals import paper_rank_score, SCRNA_PHRASES

logger = logging.getLogger(__name__)


def _sc_score(text: str) -> float:
    """Small boost when paper mentions single-cell/spatial terms."""
    lower = text.lower()
    hits = sum(1 for t in SCRNA_PHRASES if t in lower)
    return min(0.15, hits * 0.03)


def _rank_papers(
    papers: list[RetrievedPaper],
    user_genes: list[str],
    analysis_input: AnalysisInput | None = None,
) -> list[RetrievedPaper]:
    """
    Rank papers by evidence-signal score (assignment language near genes),
    not gene mention count alone.
    """
    scored: list[tuple[float, RetrievedPaper]] = []

    for paper in papers:
        score, reason = paper_rank_score(paper, user_genes, analysis_input)
        combined = " ".join(filter(None, [paper.title or "", paper.abstract or "", paper.full_text or ""]))
        score += _sc_score(combined)
        paper.retrieval_reason = reason
        scored.append((score, paper))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored]


def run_retrieval_agent(analysis_input: AnalysisInput) -> RetrievalResult:
    """
    Run the retrieval agent:
    1. Build queries and search PubMed.
    2. Optionally enrich with PMC full text.
    3. Rank and return RetrievalResult.
    """
    from maples.literature.pubmed import retrieve_papers
    from maples.literature.pmc import enrich_with_fulltext

    # --- Step 1: PubMed retrieval ---
    result = retrieve_papers(analysis_input, papers_per_gene=cfg.PAPERS_PER_GENE)
    papers = _rank_papers(result.retrieved_papers, analysis_input.markers, analysis_input)

    # Cap at MAX_EVIDENCE_PAPERS after ranking (best papers first)
    if len(papers) > cfg.MAX_EVIDENCE_PAPERS:
        top = papers[: cfg.MAX_EVIDENCE_PAPERS]
        # single-cell pool papers are always kept: they were fetched on purpose
        papers = top + [p for p in papers[cfg.MAX_EVIDENCE_PAPERS:] if p.in_pool]
        result.audit_notes.append(
            f"Capped to {cfg.MAX_EVIDENCE_PAPERS} papers (MAX_EVIDENCE_PAPERS) plus single-cell pool papers"
        )

    # --- Step 2: Full-text enrichment on top-ranked papers ---
    if cfg.ENABLE_FULLTEXT and papers:
        from maples.runtime import activity

        activity.report("phase", text="Fetching full text for the best-matching papers")
        try:
            enrich_with_fulltext(papers, max_papers=cfg.LLM_EXTRACTION_MAX_PAPERS)
            pool = [p for p in papers if p.in_pool]
            if pool:
                activity.report("phase", text="Fetching full text for the single-cell papers")
                enrich_with_fulltext(pool, max_papers=cfg.SC_POOL_READ_MAX)
            ft_count = sum(1 for p in papers if p.full_text)
            result.fulltext_count = ft_count
            result.audit_notes.append(f"Full text fetched for {ft_count} papers")
        except Exception as exc:
            logger.warning("Full-text enrichment failed: %s", exc)
            result.audit_notes.append(f"Full-text enrichment error: {exc}")

    # --- Step 3: Re-rank after full-text enrichment ---
    result.retrieved_papers = _rank_papers(papers, analysis_input.markers, analysis_input)
    result.total_after_dedup = len(result.retrieved_papers)

    # Re-index retrieval_rank after sorting
    for i, paper in enumerate(result.retrieved_papers):
        paper.retrieval_rank = i

    logger.info(
        "Retrieval agent: %d papers, %d with full text",
        len(result.retrieved_papers),
        result.fulltext_count,
    )
    return result
