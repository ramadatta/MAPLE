"""Optional PubTator Central integration stub.

When MAPLE_PUBTATOR_ENABLED=true, gene/entity detection can be enriched
via PubTator annotations. Disabled by default; failures are non-fatal.
"""
from __future__ import annotations

import logging

from maple import config as cfg

logger = logging.getLogger(__name__)


def enrich_genes_from_pubtator(pmid: str, user_genes: list[str]) -> list[str]:
    """
    Return user genes confirmed or aliased via PubTator for a PMID.

    Stub: returns input genes unchanged when disabled or unavailable.
    """
    if not cfg.PUBTATOR_ENABLED or not pmid:
        return list(user_genes)

    try:
        # Phase 2: call PubTator Central REST API
        logger.debug("PubTator enabled but not implemented; using local gene matching")
    except Exception as exc:
        logger.warning("PubTator lookup failed for PMID %s: %s", pmid, exc)

    return list(user_genes)
