"""Adapter: RetrievedPaper → PMC full text enrichment."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maple.models import RetrievedPaper

logger = logging.getLogger(__name__)


def enrich_with_fulltext(papers: list, max_papers: int = 20) -> None:
    """
    Try to get PMC full text for papers. Modifies papers in place by setting full_text.

    Strategy:
    1. For papers with a known PMCID, use NCBIPMCService.full_text_for_pmcid (same NCBI host).
    2. For papers without PMCID, use EuropePMCService.full_text_for_pmid (looks up PMCID first).

    Only processes up to max_papers to stay within rate limits.
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

    processed = 0
    for paper in papers:
        if processed >= max_papers:
            break
        if paper.full_text:
            continue  # already enriched

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

        # Paywalled / not-in-PMC: try an open preprint copy by DOI, then title.
        if not full_text and preprint_service is not None:
            try:
                if getattr(paper, "doi", None):
                    full_text = preprint_service.full_text_for_doi(paper.doi)
                if not full_text and paper.title:
                    full_text = preprint_service.full_text_by_title(paper.title)
            except Exception as exc:
                logger.debug("Preprint full-text fallback failed for %s: %s", paper.pmid, exc)

        if full_text:
            paper.full_text = full_text
            processed += 1

    logger.debug("Enriched %d papers with full text (limit=%d)", processed, max_papers)
