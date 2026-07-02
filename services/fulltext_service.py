"""Europe PMC full-text retrieval (no auth) for accurate gene-mention counting.

Abstracts rarely list a full marker panel, so per-paper gene counts saturate low.
Europe PMC exposes open-access full text over a public REST API (no key), which
lets us count how many input genes a paper actually mentions across the article.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET

import requests

from models.schemas import PubMedPaper
from services.cache_service import get_cache

logger = logging.getLogger(__name__)

EPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{pmcid}/fullTextXML"
REQUEST_TIMEOUT = 15
RATE_LIMIT_SECONDS = 0.15


class EuropePMCService:
    """Fetch open-access full text from Europe PMC by PMID (cached, no auth)."""

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
            if "/fullTextXML" in url and "404" in str(exc):
                logger.debug("Europe PMC full text unavailable (%s): %s", url, exc)
            else:
                logger.warning("Europe PMC request failed (%s): %s", url, exc)
            return None

    def _lookup_open_access_pmcid(self, pmid: str) -> str:
        """Return the PMCID if the article is open-access in Europe PMC, else ''."""
        params = {
            "query": f"ext_id:{pmid} AND src:med",
            "format": "json",
            "resultType": "lite",
            "pageSize": 1,
        }
        resp = self._get(EPMC_SEARCH_URL, params)
        if resp is None:
            return ""
        try:
            results = resp.json().get("resultList", {}).get("result", [])
        except ValueError:
            return ""
        if not results:
            return ""
        res = results[0]
        pmcid = res.get("pmcid", "") or ""
        is_oa = res.get("isOpenAccess") == "Y" and res.get("inEPMC") == "Y"
        return pmcid if (pmcid and is_oa) else ""

    @staticmethod
    def _paper_from_epmc_result(res: dict) -> PubMedPaper:
        year = None
        try:
            year = int(res.get("pubYear")) if res.get("pubYear") else None
        except (TypeError, ValueError):
            year = None
        pmid = res.get("pmid", "") or res.get("id", "")
        return PubMedPaper(
            pmid=pmid,
            title=res.get("title", "").rstrip("."),
            journal=res.get("journalTitle", ""),
            year=year,
            abstract=res.get("abstractText", "") or "",
            pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            pmcid=res.get("pmcid", "") or "",
            is_open_access=res.get("isOpenAccess") == "Y",
        )

    def search_open_access(
        self,
        query: str,
        max_results: int = 25,
        *,
        open_access_only: bool = True,
    ) -> list[PubMedPaper]:
        """Search Europe PMC for full-text-readable papers (relevance-ranked).

        By default this restricts to Europe PMC open-access records. MAPLE full-text
        discovery can set ``open_access_only=False`` to include author manuscripts
        that are in PMC and readable through NCBI even when Europe PMC does not
        mark them OPEN_ACCESS:Y.
        """
        search_query = f"({query}) AND (OPEN_ACCESS:Y)" if open_access_only else f"({query})"
        params = {
            "query": search_query,
            "format": "json",
            "resultType": "core",
            "pageSize": max(1, min(max_results, 100)),
        }
        resp = self._get(EPMC_SEARCH_URL, params)
        if resp is None:
            return []
        try:
            results = resp.json().get("resultList", {}).get("result", [])
        except ValueError:
            return []
        papers = [
            self._paper_from_epmc_result(r)
            for r in results
            if r.get("pmcid") and (open_access_only or r.get("inEPMC") == "Y")
        ]
        return papers

    def _fetch_full_text_xml(self, pmcid: str) -> str:
        resp = self._get(EPMC_FULLTEXT_URL.format(source="PMC", pmcid=pmcid))
        if resp is None:
            return ""
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
        cache_key = f"epmc_fulltext_pmcid:{pmcid}"
        cached = self.cache.get("fulltext", cache_key)
        if cached is not None:
            return cached
        text = self._fetch_full_text_xml(pmcid)
        self.cache.set("fulltext", cache_key, text)
        return text

    def full_text_for_pmid(self, pmid: str) -> str:
        """Open-access full-text plain text for a PMID, or '' if unavailable. Cached."""
        if not pmid:
            return ""
        cache_key = f"epmc_fulltext:{pmid}"
        cached = self.cache.get("fulltext", cache_key)
        if cached is not None:
            return cached
        pmcid = self._lookup_open_access_pmcid(pmid)
        text = self._fetch_full_text_xml(pmcid) if pmcid else ""
        self.cache.set("fulltext", cache_key, text)
        return text

    def full_text_for_paper(self, paper: PubMedPaper) -> str:
        """Full text for a paper, using its PMCID when known (avoids a lookup)."""
        if getattr(paper, "pmcid", ""):
            return self.full_text_for_pmcid(paper.pmcid)
        return self.full_text_for_pmid(paper.pmid)
