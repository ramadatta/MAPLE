"""The single-cell pool: every PubMed paper naming 2+ of the user's genes together
with single-cell words in its title or abstract.

The regular searches keep only the top few hits per query, which favours a handful
of well-ranked papers and misses most of the focused single-cell literature (for a
10-gene endothelial panel, only 4 of 45 such papers were being read). Here the whole
pool is fetched (most relevant first if PubMed has more than the cap) and each
paper is checked locally: it must really name two *different* genes. Aliases are
merged first, so VEGFR3 and FLT4 count as one gene, not two.
"""
from __future__ import annotations

import logging
import re
from itertools import combinations

import requests

from maples import config as cfg
from maples.genes import aliases, official_symbol
from maples.models import AnalysisInput, RetrievedPaper

logger = logging.getLogger(__name__)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

SC_TERMS = (
    '("single-cell"[tiab] OR "single cell"[tiab] OR "single-nucleus"[tiab] '
    'OR "single nucleus"[tiab] OR scRNA-seq[tiab] OR snRNA-seq[tiab] '
    'OR scRNAseq[tiab] OR "spatial transcriptomics"[tiab])'
)

# PubMed treats these characters specially or cannot index them; skip such aliases.
_UNSAFE = re.compile(r"[^A-Za-z0-9 \-./]")


def gene_groups(markers: list[str]) -> list[tuple[str, list[str], list[str]]]:
    """Merge the user's genes that are names for the same gene.

    Returns (official symbol, the user's names in this group, all names to search).
    """
    groups: dict[str, tuple[list[str], list[str]]] = {}
    for gene in markers:
        sym = official_symbol(gene)
        users, names = groups.setdefault(sym, ([], []))
        users.append(gene)
        for n in (gene, sym, *aliases(gene)):
            if n.upper() not in {x.upper() for x in names}:
                names.append(n)
    return [(sym, users, names) for sym, (users, names) in groups.items()]


def _tiab(name: str) -> str:
    return f'"{name}"[tiab]' if (" " in name or "-" in name) else f"{name}[tiab]"


def build_query(groups: list[tuple[str, list[str], list[str]]]) -> str:
    """'(any two of the genes, each under any of its names) AND single-cell words'."""
    terms = []
    for _, _, names in groups:
        safe = [n for n in names if not _UNSAFE.search(n)]
        if safe:
            terms.append("(" + " OR ".join(_tiab(n) for n in safe) + ")")
    if len(terms) < 2:
        return ""
    pairs = " OR ".join(f"({a} AND {b})" for a, b in combinations(terms, 2))
    return f"({pairs}) AND {SC_TERMS}"


def _name_pattern(names: list[str]) -> re.Pattern:
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])", re.IGNORECASE)


def distinct_genes_in(text: str, groups: list[tuple[str, list[str], list[str]]]) -> list[str]:
    """The groups (as the user's first name for each) found in ``text``."""
    if not text:
        return []
    return [users[0] for _, users, names in groups if _name_pattern(names).search(text)]


def _esearch_post(backend, query: str, retmax: int) -> tuple[int, list[str]]:
    """POST is used because the pair query is long (too long for a URL with many genes)."""
    cache = getattr(backend, "cache", None)
    key = f"sc_pool:v1:{retmax}:{query}"
    if cache is not None:
        hit = cache.get("pubmed", key)
        if hit is not None:
            return hit["count"], hit["ids"]
    data = {"db": "pubmed", "term": query, "retmax": retmax, "retmode": "json", "sort": "relevance",
            "tool": getattr(backend, "tool", "maple"), "email": getattr(backend, "email", "")}
    if getattr(backend, "api_key", ""):
        data["api_key"] = backend.api_key
    if hasattr(backend, "_rate_limit"):
        backend._rate_limit()
    resp = requests.post(ESEARCH_URL, data=data, timeout=30)
    resp.raise_for_status()
    res = resp.json().get("esearchresult", {})
    count, ids = int(res.get("count", 0)), list(res.get("idlist", []))
    if cache is not None:
        cache.set("pubmed", key, {"count": count, "ids": ids})
    return count, ids


def add_sc_pool(
    retrieved_papers: list[RetrievedPaper],
    seen_pmids: set[str],
    analysis_input: AnalysisInput,
    audit_notes: list[str],
    service,
    to_retrieved,
) -> dict:
    """Fetch the pool, mark its papers ``in_pool`` and add the new ones.

    ``to_retrieved(pubmed_paper)`` converts a PubMed record to a RetrievedPaper.
    Returns the pool facts shown to the user.
    """
    if not cfg.ENABLE_SC_POOL:
        return {}
    groups = gene_groups(analysis_input.markers)[: cfg.SC_POOL_MAX_GENES]
    query = build_query(groups)
    if not query:
        return {"skipped": "needs at least 2 different genes"}
    backend = getattr(service, "backend", service)
    try:
        count, ids = _esearch_post(backend, query, cfg.SC_POOL_MAX_FETCH)
    except Exception as exc:  # the pool adds depth; a failure must not stop the run
        logger.warning("Single-cell pool search failed: %s", exc)
        audit_notes.append(f"Single-cell pool search failed: {exc}")
        return {"error": str(exc)}

    by_pmid = {p.pmid: p for p in retrieved_papers}
    missing = [i for i in ids if i not in by_pmid]
    fetched = []
    for start in range(0, len(missing), 200):
        try:
            fetched.extend(backend.fetch_papers(missing[start:start + 200]))
        except Exception as exc:
            logger.warning("Single-cell pool fetch failed: %s", exc)
    for pub in fetched:
        rp = to_retrieved(pub)
        rp.retrieval_query = "__pool__"
        by_pmid[rp.pmid] = rp

    kept = 0
    dropped = 0
    added = 0
    for pmid in ids:
        paper = by_pmid.get(pmid)
        if paper is None:
            continue
        if len(distinct_genes_in(f"{paper.title} {paper.abstract or ''}", groups)) < 2:
            dropped += 1  # PubMed matched loosely, or two names of one gene
            continue
        paper.in_pool = True
        kept += 1
        if pmid not in seen_pmids:
            seen_pmids.add(pmid)
            retrieved_papers.append(paper)
            added += 1

    merged = [f"{'/'.join(users)}" for _, users, _ in groups if len(users) > 1]
    facts = {
        "in_pubmed": count,
        "fetched": len(ids),
        "kept": kept,
        "dropped": dropped,
        "new": added,
        "genes_used": len(groups),
        "merged_names": merged,
    }
    audit_notes.append(
        f"Single-cell pool: PubMed lists {count} paper(s) naming 2+ of your genes with single-cell "
        f"words; fetched {len(ids)}, kept {kept} after checking for 2 distinct genes ({added} new)"
    )
    return facts
