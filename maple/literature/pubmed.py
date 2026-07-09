"""Adapter: AnalysisInput → PubMed retrieval using existing services."""
from __future__ import annotations

import logging
import re
from typing import Optional

from maple.models import AnalysisInput, RetrievedPaper, RetrievalResult
from models.schemas import UserInput, MarkerAgentResult, PubMedQuery, PubMedPaper
from maple import config as cfg
from utils.gene_aliases import pubmed_gene_query

logger = logging.getLogger(__name__)

# Sentinel bucket prefix (mirrors agents/query_builder.py)
_DISCOVERY_BUCKET = "__discovery__"

_SPECIES_WORDS = frozenset(
    {"human", "mouse", "rat", "murine", "homo", "sapiens", "mus", "other"}
)


def _tissue_keyword(tissue: str) -> str:
    """Extract searchable tissue term, e.g. 'Human lung' -> 'lung'."""
    import re
    parts = re.split(r"[\s,]+", tissue.strip().lower())
    for part in reversed(parts):
        if part and part not in _SPECIES_WORDS:
            return part
    return tissue.strip()


def _disease_keyword(disease: str) -> str:
    """Shorten a long disease string for PubMed (no disease-specific priors)."""
    words = disease.split()
    if len(words) > 4:
        return " ".join(words[:4])
    return disease


def _add_query(
    queries: list[PubMedQuery],
    seen: set[str],
    query: str,
    gene: str,
    qtype: str,
    priority: int,
    cell_type: str | None = None,
) -> None:
    normalized = query.strip().lower()
    if normalized in seen:
        return
    seen.add(normalized)
    queries.append(
        PubMedQuery(
            query=query,
            gene=gene,
            candidate_cell_type=cell_type,
            query_type=qtype,
            priority=priority,
        )
    )


def _title_abstract_gene_query(gene: str) -> str:
    """Precise gene-symbol query for abstracts that are not fully Gene-indexed."""
    symbol = re.sub(r"[^A-Za-z0-9-]", "", gene.upper().strip())
    if not symbol:
        return pubmed_gene_query(gene)
    return f"({pubmed_gene_query(gene)} OR {symbol}[Title/Abstract])"


def _title_abstract_symbol_query(gene: str) -> str:
    """Simple title/abstract symbol query for marker-panel co-mention searches."""
    symbol = re.sub(r"[^A-Za-z0-9-]", "", gene.upper().strip())
    return f"{symbol}[Title/Abstract]" if symbol else pubmed_gene_query(gene)


def _plain_gene(gene: str) -> str:
    """Plain gene symbol for Europe PMC full-text queries."""
    return re.sub(r"[^A-Za-z0-9-]", "", gene.upper().strip())


def _gene_combos(genes: list[str], size: int = 3, max_combos: int = 4) -> list[tuple[str, ...]]:
    """Sliding-window gene groups for marker-panel co-mention discovery."""
    uniq = list(dict.fromkeys(g for g in genes if g))
    if len(uniq) < size:
        return [tuple(uniq)] if len(uniq) >= 2 else []
    combos = [tuple(uniq[i : i + size]) for i in range(0, len(uniq) - size + 1)]
    return combos[:max_combos]


def _gene_pairs(genes: list[str], max_pairs: int = 8) -> list[tuple[str, str]]:
    """Adjacent plus anchor gene pairs for less brittle PubMed discovery."""
    uniq = list(dict.fromkeys(g for g in genes if g))
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(a: str, b: str) -> None:
        key = tuple(sorted((a, b)))
        if a != b and key not in seen:
            seen.add(key)
            pairs.append((a, b))

    for i in range(len(uniq) - 1):
        add(uniq[i], uniq[i + 1])
    if uniq:
        for gene in uniq[1:]:
            add(uniq[0], gene)
    return pairs[:max_pairs]


def _panel_context_terms() -> str:
    """Generic single-cell / cell-type context terms (no tissue or disease priors)."""
    return (
        '"single-cell" OR "single cell" OR "RNA-seq" OR scRNA-seq OR snRNA-seq OR '
        '"spatial transcriptomics" OR "cell population" OR "cell type" OR "cell types" OR '
        '"cell state" OR "cell atlas" OR "marker genes" OR "marker gene" OR "cell subtype"'
    )


def _add_panel_discovery_queries(
    queries: list[PubMedQuery],
    seen: set[str],
    markers: list[str],
    *,
    tissue_kw: str = "",
    start_idx: int = 0,
) -> int:
    """Add marker-combination queries that can find full-panel papers."""
    disc_idx = start_idx

    for combo in _gene_combos(markers, size=3, max_combos=4):
        gene_and = " AND ".join(_title_abstract_gene_query(g) for g in combo)
        context = f" AND {tissue_kw}" if tissue_kw else ""
        _add_query(
            queries, seen,
            f"({gene_and}){context} AND ({_panel_context_terms()})",
            f"{_DISCOVERY_BUCKET}{disc_idx}", "panel_combo", 1,
        )
        disc_idx += 1

    for pair in _gene_pairs(markers, max_pairs=8):
        gene_and = " AND ".join(_title_abstract_symbol_query(g) for g in pair)
        context = f" AND {tissue_kw}" if tissue_kw else ""
        _add_query(
            queries, seen,
            f"({gene_and}){context} AND ({_panel_context_terms()})",
            f"{_DISCOVERY_BUCKET}{disc_idx}", "panel_pair", 1,
        )
        disc_idx += 1

    return disc_idx


def _fulltext_discovery_queries(analysis_input: AnalysisInput) -> list[str]:
    """Europe PMC full-text discovery queries independent of PubMed abstract ranking."""
    genes = [_plain_gene(g) for g in analysis_input.markers if _plain_gene(g)]
    if len(genes) < 2:
        return []

    queries: list[str] = []
    seen: set[str] = set()
    tissue_kw = _tissue_keyword(analysis_input.tissue) if (analysis_input.tissue or "").strip() else ""
    cell_context = (
        '("single-cell" OR "single cell" OR "RNA-seq" OR scRNA-seq OR snRNA-seq '
        'OR "spatial transcriptomics" OR "cell population" OR "cell type" OR "cell state" '
        'OR "cell atlas" OR "marker genes" OR "cell subtype")'
    )
    if tissue_kw:
        cell_context = f"{tissue_kw} AND {cell_context}"

    def add(query: str) -> None:
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        if normalized not in seen:
            seen.add(normalized)
            queries.append(query)

    for combo in _gene_combos(genes, size=3, max_combos=4):
        add(" AND ".join(combo) + f" AND {cell_context}")

    for pair in _gene_pairs(genes, max_pairs=8):
        add(" AND ".join(pair) + f" AND {cell_context}")

    return queries[:12]


def _build_discovery_queries(analysis_input: AnalysisInput) -> list[PubMedQuery]:
    """Gene-centric PubMed queries — no tissue/disease filtering (default mode)."""
    queries: list[PubMedQuery] = []
    seen: set[str] = set()

    if len(analysis_input.markers) >= 2:
        top_genes = analysis_input.markers[:6]
        gene_or = " OR ".join(pubmed_gene_query(g) for g in top_genes)
        _add_query(
            queries, seen,
            f"({gene_or}) AND (single-cell OR scRNA-seq OR cell type)",
            analysis_input.markers[0], "panel_scrna", 1,
        )

    # High-precision marker-panel queries are critical for annotation. PubMed's
    # default per-gene searches often return very recent single-gene papers;
    # these combination queries surface older but more relevant population-
    # defining studies where several submitted markers are attributed together.
    disc_idx = _add_panel_discovery_queries(queries, seen, analysis_input.markers)

    if len(analysis_input.markers) >= 4:
        top_genes = analysis_input.markers[:6]
        gene_or_ta = " OR ".join(_title_abstract_gene_query(g) for g in top_genes)
        _add_query(
            queries, seen,
            f"({gene_or_ta}) AND (signature OR markers OR marker genes) AND (cell population OR cell type)",
            f"{_DISCOVERY_BUCKET}{disc_idx}", "panel_signature", 1,
        )

    for gene in analysis_input.markers:
        g = pubmed_gene_query(gene)
        _add_query(
            queries, seen,
            f"{g} AND (single-cell OR scRNA-seq OR cell type)",
            gene, "gene_scrna", 1,
        )
        _add_query(queries, seen, g, gene, "gene_broad", 3)

    queries.sort(key=lambda q: (q.gene, q.priority))
    return queries


def _build_contextual_queries(analysis_input: AnalysisInput) -> list[PubMedQuery]:
    """Optional tissue/disease-aware queries when MAPLE_USE_USER_CONTEXT=true."""
    queries: list[PubMedQuery] = []
    seen: set[str] = set()

    tissue_kw = _tissue_keyword(analysis_input.tissue) if (analysis_input.tissue or "").strip() else ""
    disease_kw = _disease_keyword(analysis_input.disease) if (analysis_input.disease or "").strip() else ""

    if len(analysis_input.markers) >= 2:
        top_genes = analysis_input.markers[:5]
        gene_or = " OR ".join(pubmed_gene_query(g) for g in top_genes)
        if tissue_kw:
            _add_query(
                queries, seen,
                f"({gene_or}) AND {tissue_kw} AND (single-cell OR scRNA-seq OR cell type)",
                analysis_input.markers[0], "broad_cluster", 2,
            )
            if disease_kw:
                _add_query(
                    queries, seen,
                    f"({gene_or}) AND {tissue_kw} AND {disease_kw} AND (single-cell OR scRNA-seq)",
                    analysis_input.markers[0], "broad_cluster_disease", 1,
                )
        else:
            _add_query(
                queries, seen,
                f"({gene_or}) AND (single-cell OR scRNA-seq OR cell type)",
                analysis_input.markers[0], "broad_cluster", 2,
            )

    disc_idx = _add_panel_discovery_queries(
        queries, seen, analysis_input.markers, tissue_kw=tissue_kw
    )

    for gene in analysis_input.markers:
        g = pubmed_gene_query(gene)
        tiers: list[tuple[str, int]] = []
        if tissue_kw:
            tiers.append((f"{g} AND {tissue_kw} AND (single-cell OR scRNA-seq)", 1))
            tiers.append((f"{g} AND {tissue_kw}", 3))
            if disease_kw:
                tiers.append((f"{g} AND {tissue_kw} AND {disease_kw}", 2))
        else:
            tiers.append((f"{g} AND (single-cell OR scRNA-seq OR cell type marker)", 1))
            if disease_kw:
                tiers.append((f"{g} AND {disease_kw}", 2))
        tiers.append((g, 5))
        for q, priority in tiers:
            _add_query(queries, seen, q, gene, "gene_query", priority)

    if tissue_kw and disease_kw:
        disc_q = f"{tissue_kw} AND {disease_kw} AND (single-cell OR scRNA-seq OR atlas)"
    elif tissue_kw:
        disc_q = f"{tissue_kw} AND (single-cell OR scRNA-seq OR atlas)"
    else:
        disc_q = "single-cell AND scRNA-seq AND atlas"
    _add_query(queries, seen, disc_q, f"{_DISCOVERY_BUCKET}0", "discovery", 1)

    queries.sort(key=lambda q: (q.gene, q.priority))
    return queries


def _build_simple_queries(analysis_input: AnalysisInput, papers_per_gene: int = 5) -> list[PubMedQuery]:
    """
    Build PubMed queries from marker genes.

    Default (discovery mode): gene-only queries across the literature.
    Contextual mode (tissue/disease-focused queries) activates automatically when
    the user supplies context, or globally when MAPLE_USE_USER_CONTEXT=true.
    """
    if cfg.USE_USER_CONTEXT or analysis_input.has_context:
        return _build_contextual_queries(analysis_input)
    return _build_discovery_queries(analysis_input)


def _pubmed_paper_to_retrieved(
    paper,
    retrieval_query: str = "",
    retrieval_rank: int = 0,
) -> RetrievedPaper:
    """Convert a PubMedPaper (existing schema) to RetrievedPaper (maple schema)."""
    pub_year: Optional[int] = getattr(paper, "year", None)
    pmcid = getattr(paper, "pmcid", "") or None
    doi = getattr(paper, "doi", "") or None

    return RetrievedPaper(
        pmid=paper.pmid,
        pmcid=pmcid if pmcid else None,
        doi=doi,
        title=paper.title or "",
        journal=paper.journal or None,
        publication_date=str(pub_year) if pub_year else None,
        publication_year=pub_year,
        abstract=paper.abstract or None,
        full_text=None,
        source_url=getattr(paper, "pubmed_url", "") or f"https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}/",
        retrieval_query=retrieval_query,
        retrieval_rank=retrieval_rank,
    )


def _add_fulltext_discovery_papers(
    retrieved_papers: list[RetrievedPaper],
    seen_pmids: set[str],
    analysis_input: AnalysisInput,
    audit_notes: list[str],
) -> int:
    """Search OA full text before abstract ranking and append readable papers."""
    if not cfg.ENABLE_FULLTEXT or not cfg.ENABLE_FULLTEXT_DISCOVERY:
        return 0

    from services.fulltext_service import EuropePMCService
    from services.ncbi_pmc_service import NCBIPMCService

    discovery_queries = _fulltext_discovery_queries(analysis_input)
    if not discovery_queries:
        return 0

    service = EuropePMCService()
    ncbi_service = NCBIPMCService()
    added = 0
    updated = 0
    searched = 0
    max_papers = max(0, cfg.FULLTEXT_DISCOVERY_MAX_PAPERS)
    existing_by_pmid = {p.pmid: p for p in retrieved_papers}

    for query in discovery_queries:
        if added >= max_papers:
            break
        searched += 1
        try:
            papers = service.search_open_access(
                query, max_results=10, open_access_only=False
            )
        except Exception as exc:
            logger.debug("Full-text discovery search failed for %r: %s", query, exc)
            continue

        for rank, paper in enumerate(papers):
            if added >= max_papers:
                break
            if not paper.pmid:
                continue
            existing = existing_by_pmid.get(paper.pmid)
            if existing is not None and existing.full_text:
                continue

            full_text = ""
            try:
                full_text = service.full_text_for_paper(paper)
            except Exception as exc:
                logger.debug("Full-text discovery fetch failed for PMID %s: %s", paper.pmid, exc)
            if not full_text and paper.pmcid:
                try:
                    full_text = ncbi_service.full_text_for_pmcid(paper.pmcid)
                except Exception as exc:
                    logger.debug(
                        "NCBI full-text discovery fetch failed for PMID %s: %s",
                        paper.pmid,
                        exc,
                    )
            if not full_text:
                continue

            if existing is not None:
                existing.pmcid = paper.pmcid or existing.pmcid
                existing.full_text = full_text
                existing.retrieval_query = f"{_DISCOVERY_BUCKET}fulltext"
                updated += 1
                continue

            seen_pmids.add(paper.pmid)
            rp = _pubmed_paper_to_retrieved(
                paper,
                retrieval_query=f"{_DISCOVERY_BUCKET}fulltext",
                retrieval_rank=rank,
            )
            rp.full_text = full_text
            retrieved_papers.append(rp)
            existing_by_pmid[paper.pmid] = rp
            added += 1

    audit_notes.append(
        f"Full-text discovery searched {searched} OA queries; "
        f"added {added} and updated {updated} full-text paper(s)"
    )
    return added + updated


def _add_preprint_discovery_papers(
    retrieved_papers: list[RetrievedPaper],
    seen_pmids: set[str],
    seen_dois: set[str],
    analysis_input: AnalysisInput,
    audit_notes: list[str],
) -> int:
    """Discover bioRxiv/medRxiv preprints for the marker panel and attach full text."""
    if not cfg.ENABLE_PREPRINTS:
        return 0
    from services.preprint_service import PreprintService

    queries = _fulltext_discovery_queries(analysis_input)
    if not queries:
        return 0

    service = PreprintService()
    added = 0
    max_papers = max(0, cfg.PREPRINT_DISCOVERY_MAX_PAPERS)
    for query in queries:
        if added >= max_papers:
            break
        for paper in service.search_preprints(query, max_results=10):
            if added >= max_papers:
                break
            doi = paper.doi
            if not doi or doi in seen_dois:
                continue
            if paper.pmid and paper.pmid in seen_pmids:
                continue
            full_text = service.full_text_for_doi(doi)
            if not full_text:
                continue
            seen_dois.add(doi)
            if paper.pmid:
                seen_pmids.add(paper.pmid)
            rp = _pubmed_paper_to_retrieved(paper, retrieval_query=f"{_DISCOVERY_BUCKET}preprint")
            rp.full_text = full_text
            if not rp.pmid:
                rp.pmid = doi
            retrieved_papers.append(rp)
            added += 1

    audit_notes.append(
        f"Preprint discovery added {added} bioRxiv/medRxiv full-text paper(s)"
    )
    return added


def _open_fulltext_for(pmcid, doi, title, ncbi, epmc, preprint) -> str:
    """Fetch open full text for a paper from PMC, then an open preprint copy."""
    ft = ""
    if pmcid:
        try:
            ft = ncbi.full_text_for_pmcid(pmcid) or epmc.full_text_for_pmcid(pmcid)
        except Exception:
            ft = ""
    if not ft and doi and doi.startswith("10.1101"):
        ft = preprint.full_text_for_doi(doi)
    if not ft and title:
        ft = preprint.full_text_by_title(title)
    return ft


def _add_openalex_discovery_papers(
    retrieved_papers: list[RetrievedPaper],
    seen_pmids: set[str],
    seen_dois: set[str],
    analysis_input: AnalysisInput,
    audit_notes: list[str],
) -> int:
    """Find body-only papers via OpenAlex full-text search; attach open full text."""
    if not cfg.ENABLE_OPENALEX or not analysis_input.markers:
        return 0
    from services.openalex_service import OpenAlexService
    from services.fulltext_service import EuropePMCService
    from services.ncbi_pmc_service import NCBIPMCService
    from services.preprint_service import PreprintService

    works = OpenAlexService(cfg.OPENALEX_MAILTO).search_fulltext(
        " ".join(analysis_input.markers), cfg.OPENALEX_MAX_PAPERS
    )
    if not works:
        audit_notes.append("OpenAlex full-text search returned no results")
        return 0

    logger.info("OpenAlex returned %d work(s)", len(works))
    epmc = EuropePMCService()
    ncbi = NCBIPMCService()
    preprint = PreprintService()
    added = 0

    for w in works:
        pmid = w.pmid or ""
        doi = w.doi or ""
        if (pmid and pmid in seen_pmids) or (doi and doi in seen_dois):
            continue
        full_text = _open_fulltext_for(w.pmcid, doi, w.title, ncbi, epmc, preprint)
        if not full_text:
            continue  # nothing openly readable; skip so evidence stays grounded
        if pmid:
            seen_pmids.add(pmid)
        if doi:
            seen_dois.add(doi)
        rp = _pubmed_paper_to_retrieved(
            w if pmid else w.model_copy(update={"pmid": doi or f"openalex:{abs(hash(w.title)) % 10**9}"}),
            retrieval_query=f"{_DISCOVERY_BUCKET}openalex",
        )
        rp.full_text = full_text
        retrieved_papers.append(rp)
        added += 1

    audit_notes.append(
        f"OpenAlex discovery added {added} full-text paper(s) from {len(works)} work(s)"
    )
    return added


def _add_scholar_discovery_papers(
    retrieved_papers: list[RetrievedPaper],
    seen_pmids: set[str],
    seen_dois: set[str],
    analysis_input: AnalysisInput,
    audit_notes: list[str],
) -> int:
    """Use Google Scholar (full-text index) to find papers whose markers are body-only.

    Scholar is a finder only: each hit is resolved to an open copy (PMC or preprint)
    and only papers with readable full text are added, so extraction stays grounded.
    """
    if not cfg.ENABLE_SCHOLAR or not analysis_input.markers:
        return 0
    from services.scholar_service import ScholarService
    from services.fulltext_service import EuropePMCService
    from services.ncbi_pmc_service import NCBIPMCService
    from services.preprint_service import PreprintService, _titles_match

    scholar = ScholarService(cfg.SMITHERY_SCHOLAR_URL, cfg.SMITHERY_API_KEY, cfg.SCHOLAR_TIMEOUT_SECONDS)
    hits = scholar.search(" ".join(analysis_input.markers), cfg.SCHOLAR_MAX_PAPERS)
    if not hits:
        audit_notes.append("Google Scholar (Smithery) returned no usable results")
        return 0

    logger.info(
        "Scholar returned %d hit(s): %s",
        len(hits),
        [(h.get("title") or "")[:70] for h in hits],
    )

    epmc = EuropePMCService()
    ncbi = NCBIPMCService()
    preprint = PreprintService()
    added = 0

    for hit in hits:
        title = hit.get("title") or ""
        if not title:
            continue
        pmid = pmcid = doi = ""
        abstract = hit.get("snippet") or ""
        full_text = ""

        # 1. Resolve identity + open-access full text via Europe PMC by title.
        try:
            matches = epmc.search_open_access(f'TITLE:"{title}"', max_results=1, open_access_only=False)
        except Exception:
            matches = []
        match = matches[0] if (matches and _titles_match(title, matches[0].title)) else None
        if match:
            pmid = match.pmid or ""
            pmcid = match.pmcid or ""
            abstract = match.abstract or abstract
            if pmcid:
                try:
                    full_text = ncbi.full_text_for_pmcid(pmcid) or epmc.full_text_for_pmcid(pmcid)
                except Exception:
                    full_text = ""

        # 2. Fall back to an open preprint body.
        if not full_text:
            doi = preprint.resolve_preprint_doi(title)
            if doi:
                full_text = preprint.full_text_for_doi(doi)

        if (pmid and pmid in seen_pmids) or (doi and doi in seen_dois):
            continue
        if not full_text:
            logger.info("Scholar hit skipped (no open full text found): %r", title[:80])
            continue  # a Scholar snippet alone is not extractable evidence

        logger.info(
            "Scholar hit added (%d chars, %s): %r",
            len(full_text), doi or f"PMID {pmid}", title[:80],
        )
        if pmid:
            seen_pmids.add(pmid)
        if doi:
            seen_dois.add(doi)

        pub = PubMedPaper(
            pmid=pmid or doi or f"scholar:{abs(hash(title)) % 10**9}",
            title=title,
            year=hit.get("year"),
            abstract=abstract,
            pmcid=pmcid,
            doi=doi,
            pubmed_url=(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else (hit.get("link") or "")),
            is_open_access=True,
        )
        rp = _pubmed_paper_to_retrieved(pub, retrieval_query=f"{_DISCOVERY_BUCKET}scholar")
        rp.full_text = full_text
        retrieved_papers.append(rp)
        added += 1

    audit_notes.append(
        f"Google Scholar (Smithery) discovery added {added} full-text paper(s) from {len(hits)} hit(s)"
    )
    return added


def retrieve_papers(analysis_input: AnalysisInput, papers_per_gene: int = 5) -> RetrievalResult:
    """Build queries from input, retrieve from PubMed, return RetrievalResult."""
    from services.pubmed_service import PubMedService

    audit_notes: list[str] = []

    queries = _build_simple_queries(analysis_input, papers_per_gene)
    queries_used = list(dict.fromkeys(q.query for q in queries))
    audit_notes.append(f"Built {len(queries)} queries for {len(analysis_input.markers)} markers")

    service = PubMedService()
    try:
        gene_papers_map, warnings = service.retrieve_for_queries(
            queries, papers_per_query=papers_per_gene
        )
        for w in warnings:
            audit_notes.append(w)
    except Exception as exc:
        logger.error("PubMed retrieval failed: %s", exc, exc_info=True)
        audit_notes.append(f"PubMed retrieval error: {exc}")
        return RetrievalResult(
            queries_used=queries_used,
            audit_notes=audit_notes,
        )

    # Deduplicate by PMID while preserving insertion order
    seen_pmids: set[str] = set()
    seen_dois: set[str] = set()
    retrieved_papers: list[RetrievedPaper] = []

    for gene, papers in gene_papers_map.items():
        for rank, paper in enumerate(papers):
            if not paper.pmid or paper.pmid in seen_pmids:
                continue
            seen_pmids.add(paper.pmid)
            rp = _pubmed_paper_to_retrieved(paper, retrieval_query=gene, retrieval_rank=rank)
            retrieved_papers.append(rp)

    ft_discovery_added = _add_fulltext_discovery_papers(
        retrieved_papers, seen_pmids, analysis_input, audit_notes
    )
    # Full-text-aware discovery for papers whose markers appear only in the body
    # and are missed by abstract search: OpenAlex (primary finder) + Scholar + preprints.
    openalex_added = _add_openalex_discovery_papers(
        retrieved_papers, seen_pmids, seen_dois, analysis_input, audit_notes
    )
    scholar_added = _add_scholar_discovery_papers(
        retrieved_papers, seen_pmids, seen_dois, analysis_input, audit_notes
    )
    preprint_added = _add_preprint_discovery_papers(
        retrieved_papers, seen_pmids, seen_dois, analysis_input, audit_notes
    )
    fulltext_added = ft_discovery_added + openalex_added + scholar_added + preprint_added

    # Surface discovery outcome at the top so it shows in the UI reasoning trace.
    audit_notes.insert(
        0,
        f"Full-text-aware discovery added {fulltext_added} paper(s): "
        f"OpenAlex={openalex_added}, Scholar={scholar_added}, "
        f"preprints={preprint_added}, OA-search={ft_discovery_added}",
    )
    audit_notes.append(
        f"Deduplicated to {len(retrieved_papers)} unique papers from {sum(len(v) for v in gene_papers_map.values())} total"
    )

    return RetrievalResult(
        retrieved_papers=retrieved_papers,
        total_searched=sum(len(v) for v in gene_papers_map.values()) + fulltext_added,
        total_after_dedup=len(retrieved_papers),
        fulltext_count=0,  # fulltext enrichment happens in retrieval_agent
        queries_used=queries_used,
        audit_notes=audit_notes,
    )
