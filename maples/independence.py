"""How independent is the evidence?

Eight papers can rest on two datasets, or come from one lab, or be reviews
repeating one original finding. This module labels each evidence row so the app
can count *independent sources* instead of papers:

* paper_kind   primary / review / commentary (from PubMed or OpenAlex publication
               types; a title fallback only when no type is known)
* is_preprint  not yet peer reviewed
* datasets     public accession numbers found in the paper (GEO, ArrayExpress,
               SRA/ENA/BioProject, EGA, GSA, dbGaP, Synapse)
* lab          the last author, as a stand-in for the lab
* source_id    papers that share a dataset or a lab get the same id, so they count
               once. Reviews and commentaries never count.
* cites        earlier work cited inside the quote itself ("(Adams et al., 2020)"),
               which marks the quote as second-hand even in a primary paper
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

from maples.models import EvidenceRow, RetrievedPaper

_REVIEW_TYPES = {
    "review", "systematic review", "meta-analysis", "scoping review", "review-article",
    "review article", "literature review", "book-chapter", "book chapter",
}
_COMMENTARY_TYPES = {
    "comment", "editorial", "letter", "news", "commentary", "editorial-comment",
    "published erratum", "erratum", "retraction of publication", "retracted publication",
}
_PREPRINT_TYPES = {"preprint", "posted-content"}

# Public data repositories. Each pattern captures the accession as written.
_ACCESSION_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p)
    for p in (
        r"\bGSE\d{3,8}\b",                 # GEO series
        r"\bE-[A-Z]{4}-\d{1,6}\b",         # ArrayExpress / BioStudies
        r"\bPRJ(?:NA|EB|DB)\d{3,9}\b",     # BioProject (NCBI / ENA / DDBJ)
        r"\b[SED]RP\d{6,9}\b",             # SRA / ENA / DDBJ study
        r"\bEGA[SD]\d{11}\b",              # European Genome-phenome Archive
        r"\b(?:HRA|CRA)\d{6}\b",           # Genome Sequence Archive (China)
        r"\bphs\d{6}\b",                   # dbGaP
        r"\bsyn\d{7,9}\b",                 # Synapse
    )
)

_CITATION_RE = re.compile(
    r"\b([A-Z][A-Za-z'\-]+(?: (?:and|&) [A-Z][A-Za-z'\-]+| et al\.?)),? \(?((?:19|20)\d{2})[a-z]?\)?"
)
_NUMERIC_CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")


def paper_kind(publication_types: Iterable[str], title: str = "") -> str:
    types = {t.strip().lower() for t in publication_types if t}
    if types & _REVIEW_TYPES:
        return "review"
    if types & _COMMENTARY_TYPES and not ({"journal article", "research-article", "article"} & types):
        return "commentary"
    if not types and re.search(r"\breview\b", title or "", re.I):
        return "review"
    return "primary"


def is_preprint(publication_types: Iterable[str], doi: Optional[str] = None, journal: Optional[str] = None) -> bool:
    types = {t.strip().lower() for t in publication_types if t}
    if types & _PREPRINT_TYPES:
        return True
    if (doi or "").startswith("10.1101/"):
        return True
    return (journal or "").strip().lower() in {"biorxiv", "medrxiv", "research square", "ssrn"}


def find_accessions(text: Optional[str]) -> list[str]:
    """Dataset accession numbers mentioned in ``text``, in first-seen order."""
    if not text:
        return []
    found: dict[str, None] = {}
    for pattern in _ACCESSION_PATTERNS:
        for match in pattern.finditer(text):
            found[match.group(0)] = None
    return list(found)


def lab_key(authors: list[str]) -> str:
    """Last author as 'lastname f' (accents removed), or '' when unknown.

    Handles "Jane Q Smith" (PubMed / OpenAlex) and "Smith JQ" (Europe PMC).
    """
    if not authors:
        return ""
    name = unicodedata.normalize("NFKD", authors[-1]).encode("ascii", "ignore").decode().strip()
    parts = [p for p in re.split(r"[\s.]+", name) if p]
    if not parts:
        return ""
    if len(parts) >= 2 and parts[-1].isupper() and len(parts[-1]) <= 3:
        last, first = parts[-2], parts[-1]        # "Smith JQ"
    else:
        last, first = parts[-1], parts[0]         # "Jane Q Smith"
    return f"{last.lower()} {first[0].lower()}"


def quote_citations(snippet: str) -> list[str]:
    """Earlier work cited inside a quote: 'Adams et al., 2020' or 'reference [12]'."""
    cites = [f"{m.group(1)}, {m.group(2)}" for m in _CITATION_RE.finditer(snippet or "")]
    cites += [f"reference [{m.group(1)}]" for m in _NUMERIC_CITE_RE.finditer(snippet or "")]
    return list(dict.fromkeys(cites))


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def annotate_independence(rows: list[EvidenceRow], papers: list[RetrievedPaper]) -> dict:
    """Fill the independence fields on ``rows``. Returns summary counts."""
    by_id = {p.pmid: p for p in papers}
    uf = _UnionFind()
    pmids_primary: set[str] = set()

    for row in rows:
        paper = by_id.get(row.pmid)
        types = paper.publication_types if paper else []
        row.paper_kind = paper_kind(types, row.paper_title)  # type: ignore[assignment]
        row.is_preprint = is_preprint(types, row.doi, row.journal)
        text = " ".join(filter(None, [paper.abstract if paper else "", paper.full_text if paper else ""]))
        row.datasets = find_accessions(text)
        row.lab = lab_key(paper.authors) if paper else ""
        row.cites = quote_citations(row.evidence_snippet)
        if row.paper_kind == "primary":
            pmids_primary.add(row.pmid)

    # Papers sharing any dataset or the same last author form one source.
    for row in rows:
        if row.paper_kind != "primary":
            continue
        node = f"paper:{row.pmid}"
        uf.find(node)
        for accession in row.datasets:
            uf.union(node, f"data:{accession}")
        if row.lab:
            uf.union(node, f"lab:{row.lab}")

    for row in rows:
        row.source_id = uf.find(f"paper:{row.pmid}") if row.paper_kind == "primary" else ""

    primary = [r for r in rows if r.paper_kind == "primary"]
    return {
        "papers": len({r.pmid for r in rows}),
        "primary_papers": len(pmids_primary),
        "reviews": len({r.pmid for r in rows if r.paper_kind == "review"}),
        "commentaries": len({r.pmid for r in rows if r.paper_kind == "commentary"}),
        "preprints": len({r.pmid for r in rows if r.is_preprint}),
        "independent_sources": len({r.source_id for r in primary}),
        "datasets": len({a for r in primary for a in r.datasets}),
        "papers_with_dataset": len({r.pmid for r in primary if r.datasets}),
        "labs": len({r.lab for r in primary if r.lab}),
    }
