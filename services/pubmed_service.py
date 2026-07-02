"""PubMed retrieval via NCBI E-utilities with optional MCP backend stub."""

from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Optional, Protocol

import requests

from collections import defaultdict

from models.schemas import PubMedPaper, PubMedQuery
from services.cache_service import get_cache

logger = logging.getLogger(__name__)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RATE_LIMIT_NO_KEY = 0.34
RATE_LIMIT_WITH_KEY = 0.1
CACHE_VERSION = "v3_article_pmcid"


class PubMedBackend(Protocol):
    """Protocol for PubMed retrieval backends."""

    def search(self, query: str, max_results: int) -> list[str]:
        ...

    def fetch_papers(self, pmids: list[str]) -> list[PubMedPaper]:
        ...


class EUtilitiesBackend:
    """Default NCBI E-utilities backend."""

    def __init__(self):
        self.email = os.getenv("NCBI_EMAIL", "user@example.com")
        self.api_key = os.getenv("NCBI_API_KEY", "")
        self.tool = os.getenv("NCBI_TOOL", "maple")
        self._last_request_time = 0.0
        self.cache = get_cache()

    def _rate_limit(self) -> None:
        delay = RATE_LIMIT_WITH_KEY if self.api_key else RATE_LIMIT_NO_KEY
        elapsed = time.time() - self._last_request_time
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_time = time.time()

    def _request_with_retry(self, url: str, params: dict) -> Optional[requests.Response]:
        """HTTP GET with exponential backoff retry."""
        for attempt in range(MAX_RETRIES):
            self._rate_limit()
            try:
                resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                wait = 2**attempt
                logger.warning("NCBI request failed (attempt %d): %s", attempt + 1, exc)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait)
        return None

    def search(self, query: str, max_results: int) -> list[str]:
        """ESearch for PMIDs matching a query."""
        sort_mode = os.getenv("MAPLE_PUBMED_SORT", "relevance")
        cache_key = f"{CACHE_VERSION}:search:{sort_mode}:{query}:{max_results}"
        cached = self.cache.get("pubmed", cache_key)
        if cached is not None:
            return cached

        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "sort": sort_mode,
            "tool": self.tool,
            "email": self.email,
        }
        if self.api_key:
            params["api_key"] = self.api_key

        resp = self._request_with_retry(ESEARCH_URL, params)
        if resp is None:
            return []

        try:
            data = resp.json()
            pmids = data.get("esearchresult", {}).get("idlist", [])
            self.cache.set("pubmed", cache_key, pmids)
            return pmids
        except (ValueError, KeyError) as exc:
            logger.warning("Failed to parse ESearch response: %s", exc)
            return []

    def fetch_papers(self, pmids: list[str]) -> list[PubMedPaper]:
        """EFetch for paper metadata and abstracts."""
        if not pmids:
            return []

        cache_key = f"{CACHE_VERSION}:fetch:{','.join(sorted(pmids))}"
        cached = self.cache.get("pubmed", cache_key)
        if cached is not None:
            return [PubMedPaper.model_validate(p) for p in cached]

        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "tool": self.tool,
            "email": self.email,
        }
        if self.api_key:
            params["api_key"] = self.api_key

        resp = self._request_with_retry(EFETCH_URL, params)
        if resp is None:
            return []

        try:
            papers = self._parse_pubmed_xml(resp.text)
            self.cache.set("pubmed", cache_key, [p.model_dump() for p in papers])
            return papers
        except ET.ParseError as exc:
            logger.warning("XML parsing failed: %s", exc)
            return []

    def _parse_pubmed_xml(self, xml_text: str) -> list[PubMedPaper]:
        """Parse PubMed XML into PubMedPaper objects."""
        root = ET.fromstring(xml_text)
        papers: list[PubMedPaper] = []

        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//PMID")
            pmid = pmid_el.text if pmid_el is not None else ""

            title_el = article.find(".//ArticleTitle")
            title = self._element_text(title_el)

            journal_el = article.find(".//Journal/Title")
            journal = self._element_text(journal_el)

            year_el = article.find(".//PubDate/Year")
            year: Optional[int] = None
            if year_el is not None and year_el.text:
                try:
                    year = int(year_el.text)
                except ValueError:
                    year = None

            abstract_parts = []
            for abs_el in article.findall(".//AbstractText"):
                label = abs_el.get("Label", "")
                text = self._element_text(abs_el)
                if label:
                    abstract_parts.append(f"{label}: {text}")
                else:
                    abstract_parts.append(text)
            abstract = " ".join(abstract_parts)

            pmcid = ""
            is_open_access = False
            article_id_list = article.find("./PubmedData/ArticleIdList")
            article_ids = article_id_list.findall("./ArticleId") if article_id_list is not None else []
            for article_id in article_ids:
                id_type = (article_id.get("IdType") or "").lower()
                id_text = (article_id.text or "").strip()
                if id_type == "pmc" and id_text:
                    pmcid = id_text
                elif id_type == "doi" and id_text:
                    # PubMedPaper does not currently expose DOI, but parsing the
                    # ArticleIdList here keeps PMCID extraction scoped correctly.
                    pass
            if pmcid:
                is_open_access = True

            authors = []
            for author in article.findall(".//Author"):
                last = author.find("LastName")
                fore = author.find("ForeName")
                if last is not None and last.text:
                    name = last.text
                    if fore is not None and fore.text:
                        name = f"{fore.text} {name}"
                    authors.append(name)

            papers.append(
                PubMedPaper(
                    pmid=pmid,
                    title=title,
                    journal=journal,
                    year=year,
                    abstract=abstract,
                    authors=authors,
                    pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    pmcid=pmcid,
                    is_open_access=is_open_access,
                )
            )

        return papers

    @staticmethod
    def _element_text(element: Optional[ET.Element]) -> str:
        if element is None:
            return ""
        return "".join(element.itertext()).strip()


class MCPPubMedBackend:
    """
    Optional MCP adapter stub for future PubMed MCP server integration.

    Not used at runtime by default. Non-blocking; raises if explicitly selected
    without a configured MCP server.
    """

    def search(self, query: str, max_results: int) -> list[str]:
        raise NotImplementedError(
            "MCP PubMed backend is not configured. Use EUtilitiesBackend (default)."
        )

    def fetch_papers(self, pmids: list[str]) -> list[PubMedPaper]:
        raise NotImplementedError(
            "MCP PubMed backend is not configured. Use EUtilitiesBackend (default)."
        )


class PubMedService:
    """Facade for PubMed retrieval across queries."""

    def __init__(self, backend: Optional[PubMedBackend] = None):
        self.backend = backend or EUtilitiesBackend()
        self._session_pmids: set[str] = set()

    def retrieve_for_queries(
        self,
        queries: list[PubMedQuery],
        papers_per_query: int = 5,
    ) -> tuple[dict[str, list[PubMedPaper]], list[str]]:
        """
        Execute PubMed queries per gene with progressive broadening.

        For each gene, tries queries in priority order until enough papers are
        found. Only warns once per gene if all queries return nothing.

        Returns:
            Tuple of (gene -> papers dict, warning messages).
        """
        warnings: list[str] = []
        by_gene: dict[str, list[PubMedQuery]] = defaultdict(list)
        for query in queries:
            by_gene[query.gene].append(query)
        for gene in by_gene:
            by_gene[gene].sort(key=lambda q: q.priority)

        gene_pmids: dict[str, list[str]] = {}

        for gene, gene_queries in by_gene.items():
            collected: list[str] = []
            for query in gene_queries:
                if len(collected) >= papers_per_query:
                    break
                try:
                    need = papers_per_query - len(collected)
                    pmids = self.backend.search(query.query, need)
                    for pmid in pmids:
                        if pmid not in self._session_pmids and pmid not in collected:
                            collected.append(pmid)
                            self._session_pmids.add(pmid)
                except Exception as exc:
                    logger.warning("PubMed search failed for %s: %s", gene, exc)

            gene_pmids[gene] = collected
            if not collected:
                warnings.append(
                    f"No PubMed papers found for {gene} "
                    f"(tried {len(gene_queries)} progressively broader queries)."
                )

        all_pmids = list(self._session_pmids)
        papers_by_pmid: dict[str, PubMedPaper] = {}

        if all_pmids:
            try:
                batch_size = 50
                for i in range(0, len(all_pmids), batch_size):
                    batch = all_pmids[i : i + batch_size]
                    fetched = self.backend.fetch_papers(batch)
                    for paper in fetched:
                        papers_by_pmid[paper.pmid] = paper
            except Exception as exc:
                warnings.append(f"PubMed fetch failed: {exc}")

        result: dict[str, list[PubMedPaper]] = {}
        for gene, pmids in gene_pmids.items():
            result[gene] = [
                papers_by_pmid[pmid]
                for pmid in pmids
                if pmid in papers_by_pmid
            ]

        return result, warnings
