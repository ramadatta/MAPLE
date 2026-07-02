"""OpenAlex full-text discovery (quota-free, no API key).

OpenAlex indexes full text and preprints, so its ``fulltext.search`` filter finds
papers whose marker genes appear only in the body (missed by abstract search).
It is a *finder*: results are returned with DOI/PMID/PMCID so MAPLE's existing
PMC/bioRxiv reader can fetch the actual evidence text.
"""
from __future__ import annotations

import logging
import re
import time

import requests

from models.schemas import PubMedPaper

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
REQUEST_TIMEOUT = 20
RATE_LIMIT_SECONDS = 0.12
_HEADERS = {"User-Agent": "MAPLE/1.0 (research copilot; +https://openalex.org)"}


def _clean_doi(value: str) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", (value or "").strip(), flags=re.I)


def _digits(value: str) -> str:
    m = re.search(r"(\d+)", value or "")
    return m.group(1) if m else ""


def _pmcid(value: str) -> str:
    m = re.search(r"(PMC\d+)", value or "", re.I)
    return m.group(1).upper() if m else ""


def _reconstruct_abstract(inv_index: dict | None) -> str:
    """Rebuild an abstract from OpenAlex's inverted index (positions -> words)."""
    if not isinstance(inv_index, dict) or not inv_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv_index.items():
        if isinstance(idxs, list):
            for i in idxs:
                positions.append((i, word))
    positions.sort(key=lambda p: p[0])
    return " ".join(w for _, w in positions)[:2000]


class OpenAlexService:
    """Search OpenAlex full text for the marker panel (no auth)."""

    def __init__(self, mailto: str = ""):
        self.mailto = mailto
        self._last_request = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.time()

    def _get(self, params: dict) -> requests.Response | None:
        self._rate_limit()
        try:
            resp = requests.get(OPENALEX_WORKS_URL, params=params, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("OpenAlex request failed: %s", exc)
            return None

    @staticmethod
    def _paper_from_work(work: dict) -> PubMedPaper | None:
        title = (work.get("title") or work.get("display_name") or "").strip()
        if not title:
            return None
        ids = work.get("ids") or {}
        return PubMedPaper(
            pmid=_digits(ids.get("pmid", "")),
            title=title,
            year=work.get("publication_year"),
            abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
            pmcid=_pmcid(ids.get("pmcid", "")),
            doi=_clean_doi(work.get("doi", "") or ids.get("doi", "")),
            pubmed_url=work.get("doi") or "",
            is_open_access=bool((work.get("open_access") or {}).get("is_oa")),
        )

    def search_fulltext(self, query: str, max_results: int = 15) -> list[PubMedPaper]:
        """Return works whose full text matches the query (relevance-ranked)."""
        params = {
            "filter": f"fulltext.search:{query}",
            "per-page": max(1, min(max_results, 50)),
            "select": "id,title,display_name,doi,ids,publication_year,open_access,abstract_inverted_index",
        }
        if self.mailto:
            params["mailto"] = self.mailto
        resp = self._get(params)
        if resp is None:
            return []
        try:
            results = resp.json().get("results", [])
        except ValueError:
            return []
        papers: list[PubMedPaper] = []
        for work in results:
            paper = self._paper_from_work(work)
            if paper is not None:
                papers.append(paper)
        return papers
