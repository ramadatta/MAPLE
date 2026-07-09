"""Adapter: RetrievedPaper → PMC full text enrichment."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maple.models import RetrievedPaper

logger = logging.getLogger(__name__)


def enrich_with_fulltext(papers: list, max_papers: int = 20) -> None:
    """
    Try to get PMC full text for papers. Modifies papers in place by setting full_text.

    Strategy (per paper):
    1. For papers with a known PMCID, use NCBIPMCService.full_text_for_pmcid (same NCBI host).
    2. For papers without PMCID, use EuropePMCService.full_text_for_pmid (looks up PMCID first).
    3. Paywalled / not-in-PMC: try an open preprint copy by DOI, then title.

    The top ``max_papers`` papers lacking full text are fetched CONCURRENTLY in a
    bounded thread pool (was previously one blocking HTTP request at a time).
    """
    from services.ncbi_pmc_service import NCBIPMCService
    from services.fulltext_service import EuropePMCService
    from maple import config as cfg

    ncbi_service = NCBIPMCService()
    epmc_service = EuropePMCService()
    preprint_service = None
    if cfg.ENABLE_PREPRINTS:
        from services.preprint_service import PreprintService
        preprint_service = PreprintService()

    # Top-ranked papers that still need full text.
    candidates = []
    for paper in papers:
        if paper.full_text:
            continue
        candidates.append(paper)
        if len(candidates) >= max_papers:
            break

    if not candidates:
        logger.debug("Full-text enrichment: nothing to fetch")
        return

    def _fetch(paper) -> str:
        full_text = ""
        try:
            if paper.pmcid:
                full_text = ncbi_service.full_text_for_pmcid(paper.pmcid)
                if not full_text:
                    full_text = epmc_service.full_text_for_pmcid(paper.pmcid)
            elif paper.pmid:
                full_text = epmc_service.full_text_for_pmid(paper.pmid)
        except Exception as exc:
            logger.debug("Full-text fetch failed for PMID %s: %s", paper.pmid, exc)

        if not full_text and preprint_service is not None:
            try:
                if getattr(paper, "doi", None):
                    full_text = preprint_service.full_text_for_doi(paper.doi)
                if not full_text and paper.title:
                    full_text = preprint_service.full_text_by_title(paper.title)
            except Exception as exc:
                logger.debug("Preprint full-text fallback failed for %s: %s", paper.pmid, exc)
        return full_text or ""

    processed = 0
    max_workers = max(1, min(len(candidates), cfg.FULLTEXT_FETCH_CONCURRENCY))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_fetch, paper): paper for paper in candidates}
        for future in as_completed(future_map):
            paper = future_map[future]
            try:
                full_text = future.result()
            except Exception as exc:
                logger.debug("Full-text worker error for PMID %s: %s", paper.pmid, exc)
                full_text = ""
            if full_text:
                paper.full_text = full_text
                processed += 1

    logger.debug("Enriched %d papers with full text (limit=%d)", processed, max_papers)
