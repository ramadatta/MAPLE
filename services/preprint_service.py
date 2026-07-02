"""Preprint full-text source (bioRxiv / medRxiv) + Europe PMC preprint discovery.

Many papers are paywalled at the publisher but have an openly readable preprint.
This service (1) discovers preprints for a marker query via Europe PMC's preprint
source (SRC:PPR) and (2) fetches clean JATS full text from the bioRxiv/medRxiv API
by DOI. Preprint bodies routinely contain the full marker panel that abstracts omit.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

from models.schemas import PubMedPaper
from services.cache_service import get_cache

logger = logging.getLogger(__name__)

EPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
BIORXIV_DETAILS_URL = "https://api.biorxiv.org/details/{server}/{doi}"
REQUEST_TIMEOUT = 15
RATE_LIMIT_SECONDS = 0.15
_PREPRINT_SERVERS = ("biorxiv", "medrxiv")

# bioRxiv/medRxiv (Cloudflare) reject the default python-requests User-Agent with
# HTTP 403, so send browser-like headers for the JATS full-text fetch.
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/xml,text/xml,application/json,text/html;q=0.9,*/*;q=0.8",
}


def _norm_title(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(w) > 3}


def _titles_match(a: str, b: str) -> bool:
    ta, tb = _norm_title(a), _norm_title(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return overlap >= 0.7


class PreprintService:
    """Discover preprints and fetch their full text (cached, no auth)."""

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
            resp = requests.get(url, params=params, headers=_HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.debug("Preprint request failed (%s): %s", url, exc)
            return None

    # -- Discovery -----------------------------------------------------------

    def search_preprints(self, query: str, max_results: int = 10) -> list[PubMedPaper]:
        """Search Europe PMC's preprint corpus (SRC:PPR) for a query."""
        params = {
            "query": f"({query}) AND (SRC:PPR)",
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

        papers: list[PubMedPaper] = []
        for res in results:
            doi = (res.get("doi") or "").strip()
            if not doi.startswith("10.1101"):  # bioRxiv / medRxiv DOI prefix
                continue
            year = None
            try:
                year = int(res.get("pubYear")) if res.get("pubYear") else None
            except (TypeError, ValueError):
                year = None
            clean_title = re.sub(r"<[^>]+>", "", res.get("title", "") or "")
            clean_title = re.sub(r"\s+", " ", clean_title).strip().rstrip(".")
            papers.append(
                PubMedPaper(
                    pmid=res.get("pmid", "") or "",
                    title=clean_title,
                    journal=(res.get("bookOrReportDetails", {}) or {}).get("publisher", "") or "bioRxiv",
                    year=year,
                    abstract=res.get("abstractText", "") or "",
                    pubmed_url=f"https://doi.org/{doi}",
                    doi=doi,
                    is_open_access=True,
                )
            )
        return papers

    def resolve_preprint_doi(self, title: str) -> str:
        """Find a bioRxiv/medRxiv DOI for a paper title via Europe PMC, or ''.

        Uses a loose keyword query (not an exact-phrase match) because a paper's
        published and preprint titles often differ slightly (e.g. "single-cell"
        vs "single cell"); fuzzy title matching then confirms the hit.
        """
        if not title:
            return ""
        words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if len(w) > 3][:12]
        if len(words) < 3:
            return ""
        for hit in self.search_preprints(" ".join(words), max_results=8):
            if _titles_match(title, hit.title):
                return hit.doi
        return ""

    # -- Full text -----------------------------------------------------------

    def _jatsxml_url(self, doi: str) -> str:
        for server in _PREPRINT_SERVERS:
            resp = self._get(BIORXIV_DETAILS_URL.format(server=server, doi=doi))
            if resp is None:
                continue
            try:
                collection = resp.json().get("collection", [])
            except ValueError:
                continue
            if collection:
                # Last entry is the most recent version.
                return collection[-1].get("jatsxml", "") or ""
        return ""

    @staticmethod
    def _strip_xml(xml_text: str) -> str:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return ""
        return " ".join(t for t in root.itertext() if t and t.strip())

    def full_text_for_doi(self, doi: str) -> str:
        """Clean full text for a bioRxiv/medRxiv DOI, or '' if unavailable. Cached."""
        if not doi or not doi.startswith("10.1101"):
            return ""
        cache_key = f"preprint_fulltext:{doi}"
        cached = self.cache.get("fulltext", cache_key)
        if cached is not None:
            return cached
        text = ""
        url = self._jatsxml_url(doi)
        if url:
            resp = self._get(url)
            if resp is not None:
                text = self._strip_xml(resp.text)
        self.cache.set("fulltext", cache_key, text)
        return text

    def full_text_by_title(self, title: str) -> str:
        """Best-effort preprint full text for a paper identified by title."""
        doi = self.resolve_preprint_doi(title)
        return self.full_text_for_doi(doi) if doi else ""
