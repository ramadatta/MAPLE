"""Query Builder Agent — generate optimized PubMed search queries."""

from __future__ import annotations

import re

from models.schemas import MarkerAgentResult, PubMedQuery, UserInput
from utils.cell_type_lexicon import population_phrases
from utils.gene_aliases import pubmed_gene_query

MAX_QUERIES_PER_GENE = 6
# Sentinel bucket prefix for signature-level "discovery" queries (not per-gene).
DISCOVERY_BUCKET = "__discovery__"
_SPECIES_WORDS = frozenset(
    {"human", "mouse", "rat", "murine", "homo", "sapiens", "mus", "other"}
)


def _tissue_keyword(tissue: str) -> str:
    """Extract searchable tissue term, e.g. 'Human lung' -> 'lung'."""
    parts = re.split(r"[\s,]+", tissue.strip().lower())
    for part in reversed(parts):
        if part and part not in _SPECIES_WORDS:
            return part
    return tissue.strip()


def _disease_keyword(disease: str) -> str:
    """Shorten disease string for PubMed."""
    lower = disease.lower()
    if "idiopathic pulmonary fibrosis" in lower or lower == "ipf":
        return "pulmonary fibrosis"
    if "fibrosis" in lower:
        return "fibrosis"
    words = disease.split()
    if len(words) > 4:
        return " ".join(words[:4])
    return disease


def _gene_term(gene: str) -> str:
    """PubMed gene field search for symbol precision."""
    return pubmed_gene_query(gene)


def build_queries(
    user_input: UserInput,
    marker_result: MarkerAgentResult,
) -> list[PubMedQuery]:
    """
    Generate deduplicated PubMed queries per gene, broadest-last.

    Queries use progressive broadening so PubMed retrieval can stop once
    papers are found for each gene.  Disease context is optional; when absent
    disease-specific tiers are skipped so the tool works across any tissue.
    """
    queries: list[PubMedQuery] = []
    seen: set[str] = set()
    top_candidates = marker_result.candidates[:3]
    tissue_kw = _tissue_keyword(user_input.tissue) if user_input.tissue.strip() else ""
    disease_kw = _disease_keyword(user_input.disease) if user_input.disease.strip() else ""

    def _add(query: str, gene: str, cell_type: str | None, qtype: str, priority: int) -> None:
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

    # Broad multi-gene query (always emitted).
    if len(user_input.genes) >= 2:
        top_genes = user_input.genes[:5]
        gene_or = " OR ".join(_gene_term(g) for g in top_genes)
        if tissue_kw:
            _add(
                f"({gene_or}) AND {tissue_kw} AND (single-cell OR scRNA-seq)",
                user_input.genes[0],
                None,
                "broad_cluster",
                2,
            )
        else:
            _add(
                f"({gene_or}) AND (single-cell OR scRNA-seq)",
                user_input.genes[0],
                None,
                "broad_cluster",
                2,
            )

    for gene in user_input.genes:
        g = _gene_term(gene)
        count = 0

        tiers: list[tuple[str, str | None, str, int]] = []

        if tissue_kw:
            # Tissue-specific tiers (highest precision).
            tiers += [
                (f"{g} AND {tissue_kw} AND (single-cell OR scRNA-seq)", None, "gene_scrna_tissue", 1),
                (f"{g} AND {tissue_kw}", None, "gene_tissue", 3),
            ]
            if disease_kw:
                tiers.append(
                    (f"{g} AND {tissue_kw} AND {disease_kw}", None, "gene_tissue_disease", 2)
                )
            for candidate in top_candidates[:2]:
                tiers.append((
                    f"{g} AND {candidate.cell_type} AND {tissue_kw}",
                    candidate.cell_type,
                    "gene_celltype",
                    4,
                ))
            tiers += [
                (f"{g} AND cell type marker AND {tissue_kw}", None, "fallback_marker", 5),
            ]
        else:
            # No tissue — use broad scRNA-seq queries so the tool works without context.
            tiers += [
                (f"{g} AND (single-cell OR scRNA-seq)", None, "gene_scrna", 1),
                (f"{g} AND cell type marker", None, "fallback_marker", 3),
            ]
            if disease_kw:
                tiers.append((f"{g} AND {disease_kw}", None, "gene_disease", 2))
            for candidate in top_candidates[:2]:
                tiers.append((
                    f"{g} AND {candidate.cell_type}",
                    candidate.cell_type,
                    "gene_celltype",
                    4,
                ))

        # Always include a completely unrestricted fallback so any tissue/species/disease
        # combination in the literature can be surfaced.
        tiers.append((g, None, "gene_broad", 5))

        for query, cell_type, qtype, priority in tiers:
            if count >= MAX_QUERIES_PER_GENE:
                break
            _add(query, gene, cell_type, qtype, priority)
            count += 1

    # Discovery queries: surface papers that co-mention MANY input markers
    # (atlases, population-defining, and marker-combination papers).
    disc_idx = 0

    def _add_discovery(query: str, cell_type: str | None) -> None:
        nonlocal disc_idx
        _add(query, f"{DISCOVERY_BUCKET}{disc_idx}", cell_type, "discovery", 1)
        disc_idx += 1

    # 1. Single-cell atlas/landscape papers (tissue + disease when available).
    if tissue_kw and disease_kw:
        _add_discovery(
            f"{tissue_kw} AND {disease_kw} AND (single-cell OR scRNA-seq OR atlas OR landscape)",
            None,
        )
    elif tissue_kw:
        _add_discovery(
            f"{tissue_kw} AND (single-cell OR scRNA-seq OR atlas OR landscape)",
            None,
        )
    else:
        _add_discovery("single-cell AND scRNA-seq AND atlas", None)

    # 2. Population-defining papers, named from the top marker candidates.
    for candidate in top_candidates[:2]:
        phrases = population_phrases(candidate.cell_type)
        if not phrases:
            continue
        term = " OR ".join(f'"{p}"' for p in phrases)
        if tissue_kw:
            context = f"{disease_kw} AND {tissue_kw}" if disease_kw else tissue_kw
            _add_discovery(f"({term}) AND {context}", candidate.cell_type)
        else:
            _add_discovery(f"({term})", candidate.cell_type)

    # 3. Marker-combination papers: AND the strongest co-occurring markers.
    signature = (
        top_candidates[0].matched_genes
        if top_candidates and top_candidates[0].matched_genes
        else user_input.genes
    )
    for combo in _gene_combos(signature, size=3, max_combos=3):
        gene_and = " AND ".join(_gene_term(g) for g in combo)
        suffix = f" AND {tissue_kw}" if tissue_kw else ""
        _add_discovery(f"({gene_and}){suffix}", None)

    queries.sort(key=lambda q: (q.gene, q.priority))
    return queries


def _gene_combos(genes: list[str], size: int = 3, max_combos: int = 3) -> list[tuple[str, ...]]:
    """Sliding-window gene groups for AND-combination queries (co-mention search)."""
    uniq = list(dict.fromkeys(g for g in genes if g))
    if len(uniq) < size:
        return [tuple(uniq)] if uniq else []
    combos = [tuple(uniq[i : i + size]) for i in range(0, len(uniq) - size + 1)]
    return combos[:max_combos]
