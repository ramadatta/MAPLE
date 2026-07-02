"""NCBI PMC full-text retrieval using E-utilities (same host as working PubMed search).

When EBI Europe PMC is unreachable, fall back to NCBI's own PMC database.
NCBI E-utilities (eutils.ncbi.nlm.nih.gov) is the same host your PubMed search
already reaches, so this should work on your network.
"""

from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ET

import requests

from services.cache_service import get_cache

logger = logging.getLogger(__name__)

NCBI_EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_FETCH_URL = f"{NCBI_EUTILS_BASE}/efetch.fcgi"
REQUEST_TIMEOUT = 15
RATE_LIMIT_SECONDS = 0.1


class NCBIPMCService:
    """Fetch open-access full text from NCBI PMC by PMCID (no auth, same host as PubMed)."""

    def __init__(self):
        self.cache = get_cache()
        self._last_request = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.time()

    def _get(self, url: str, params: dict | None = None) -> requests.Response | None:
        self._rate_limit()
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("NCBI PMC request failed (%s): %s", url, exc)
            return None

    def _fetch_full_text_xml(self, pmcid: str) -> str:
        """Fetch JATS XML from NCBI PMC for a given PMCID (handles 'PMC123456' or '123456')."""
        if not pmcid:
            return ""

        # NCBI accepts PMC-prefixed IDs directly, but strip it just in case
        pmc_id = str(pmcid).strip()
        if pmc_id.startswith("PMC"):
            pmc_id = pmc_id[3:]  # Remove 'PMC' prefix

        if not pmc_id or not pmc_id.isdigit():
            logger.debug("NCBI PMC: invalid PMCID format: %r", pmcid)
            return ""

        params = {
            "db": "pmc",
            "id": pmc_id,
            "rettype": "xml",
            "tool": os.getenv("NCBI_TOOL", "maple"),
            "email": os.getenv("NCBI_EMAIL", "maple@example.com"),
        }
        resp = self._get(NCBI_FETCH_URL, params)
        if resp is None or not resp.text:
            logger.debug("NCBI PMC: no response for PMC%s", pmc_id)
            return ""

        logger.debug("NCBI PMC: PMC%s → %d chars", pmc_id, len(resp.text))
        return self._strip_xml(resp.text)

    @staticmethod
    def _strip_xml(xml_text: str) -> str:
        """Flatten JATS XML to plain text for keyword matching."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return ""
        return " ".join(t for t in root.itertext() if t and t.strip())

    def full_text_for_pmcid(self, pmcid: str) -> str:
        """Open-access full-text plain text for a known PMCID, or '' if unavailable. Cached."""
        if not pmcid:
            return ""
        cache_key = f"ncbi_pmc_fulltext:{pmcid}"
        cached = self.cache.get("fulltext", cache_key)
        if cached:
            return cached
        text = self._fetch_full_text_xml(pmcid)
        if text:
            self.cache.set("fulltext", cache_key, text)
        return text

    def full_text_for_paper(self, paper) -> str:
        """Full text for a paper using its PMCID (assumes PMCID already populated)."""
        pmcid = getattr(paper, "pmcid", "")
        if pmcid:
            return self.full_text_for_pmcid(pmcid)
        return ""
