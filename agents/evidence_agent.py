"""Evidence Extraction Agent — extract gene-to-cell-type evidence from PubMed papers."""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from agents.marker_agent import _load_marker_rules
from models.schemas import (
    EvidenceAgentResult,
    GeneEvidence,
    MarkerAgentResult,
    PubMedPaper,
    UserInput,
)
from utils.cell_type_lexicon import infer_cell_type_from_literature, load_canonical_cell_types
from utils.gene_aliases import cell_type_for_gene

ABSTRACT_UNAVAILABLE = (
    "Abstract unavailable; evidence based only on title/metadata."
)

MAX_PAPERS_PER_GENE = int(os.getenv("EVIDENCE_MAX_PAPERS_PER_GENE", "3"))
USE_LLM_EVIDENCE = os.getenv("EVIDENCE_USE_LLM", "false").lower() in ("1", "true", "yes")
MAX_LLM_GENES = int(os.getenv("EVIDENCE_LLM_MAX_GENES", "4"))


class _GeneEvidenceBatch(BaseModel):
    """LLM batch output schema — one gene, multiple papers."""

    evidence_items: list[GeneEvidence] = Field(default_factory=list)


def _build_paper_context(paper: PubMedPaper) -> str:
    authors = ", ".join(paper.authors[:5]) if paper.authors else "Unknown"
    abstract = paper.abstract if paper.abstract else "[No abstract available]"
    return (
        f"PMID: {paper.pmid}\n"
        f"Title: {paper.title}\n"
        f"Journal: {paper.journal}\n"
        f"Year: {paper.year}\n"
        f"Authors: {authors}\n"
        f"Abstract: {abstract}"
    )


def _match_context(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in keywords)


def _tissue_keywords(tissue: str) -> list[str]:
    parts = re.split(r"[\s,]+", tissue.lower())
    skip = {"human", "mouse", "rat", "other"}
    return [p for p in parts if p and p not in skip]


def _disease_keywords(disease: str) -> list[str]:
    if not disease.strip():
        return []
    lower = disease.lower()
    kws = [w for w in re.split(r"[\s,]+", lower) if len(w) > 3]
    return list(dict.fromkeys(kws))


def _gene_literature_context(gene: str, paper: PubMedPaper) -> str:
    """Return the sentence mentioning the gene, or title+abstract as fallback."""
    gene_upper = gene.upper()
    if paper.abstract:
        for sentence in re.split(r"(?<=[.!?])\s+", paper.abstract):
            if gene_upper in sentence.upper():
                return sentence.strip()
    return f"{paper.title}. {paper.abstract}".strip()


def _predicted_cell_type_from_literature(
    gene: str,
    paper: PubMedPaper,
    marker_result: MarkerAgentResult,
) -> tuple[str, str, str]:
    """
    Assign cell type from PubMed text first, marker rules only as fallback.

    Returns (cell_type, source, reasoning_fragment).
    """
    canonical_types = load_canonical_cell_types()
    gene_context = _gene_literature_context(gene, paper)
    full_text = f"{paper.title}. {paper.abstract}"

    # Prefer a cell-type phrase consistent with the gene's own marker lineage.
    # This stops a broadly-expressed gene (e.g. FN1) from being attributed to an
    # incidentally-mentioned cell type (e.g. macrophages) when the paper also
    # names the lineage the gene actually marks.
    rules = _load_marker_rules()
    lineage = cell_type_for_gene(gene, rules)
    if lineage:
        for scope, text in (("gene context", gene_context), ("full paper", full_text)):
            cell_type, phrase = infer_cell_type_from_literature(text, [lineage])
            if cell_type:
                return (
                    cell_type,
                    "literature",
                    f"Inferred from PubMed {scope} (matched '{phrase}', "
                    f"consistent with {gene} marker lineage).",
                )

    for scope, text in (("gene context", gene_context), ("full paper", full_text)):
        cell_type, phrase = infer_cell_type_from_literature(text, canonical_types)
        if cell_type:
            return (
                cell_type,
                "literature",
                f"Inferred from PubMed {scope} (matched '{phrase}').",
            )

    if lineage:
        return (
            lineage,
            "marker_fallback",
            "No cell-type phrase in paper; used canonical marker association for gene.",
        )
    if marker_result.candidates:
        return (
            marker_result.candidates[0].cell_type,
            "marker_fallback",
            "No cell-type phrase in paper; used top marker-rule candidate.",
        )
    return "Unknown", "none", "No cell-type phrase in paper and no marker association."


def _infer_cell_type_from_text(text: str, marker_result: MarkerAgentResult) -> str:
    lower = text.lower()
    for candidate in marker_result.candidates[:5]:
        if candidate.cell_type.lower() in lower:
            return candidate.cell_type
    if marker_result.candidates:
        return marker_result.candidates[0].cell_type
    return "Unknown"


def _extract_relevant_sentence(gene: str, paper: PubMedPaper) -> str:
    """Pick a short sentence from the abstract mentioning the gene, else use title."""
    gene_upper = gene.upper()
    if paper.abstract:
        for sentence in re.split(r"(?<=[.!?])\s+", paper.abstract):
            if gene_upper in sentence.upper():
                return sentence.strip()[:500]
        return paper.abstract[:400].strip()
    return paper.title[:400].strip() or ABSTRACT_UNAVAILABLE


def _heuristic_evidence(
    gene: str,
    paper: PubMedPaper,
    marker_result: MarkerAgentResult,
    user_input: UserInput,
) -> GeneEvidence | None:
    """Fast deterministic evidence when gene appears in retrieved paper text."""
    haystack = f"{paper.title} {paper.abstract}"
    if gene.upper() not in haystack.upper():
        return None

    combined = haystack.lower()
    tissue_match: bool | str = _match_context(combined, _tissue_keywords(user_input.tissue))
    disease_match: bool | str = _match_context(combined, _disease_keywords(user_input.disease))
    species_match: bool | str = "unknown"
    if user_input.species.lower() == "human" and _match_context(
        combined, ["human", "patient", "clinical", "homo sapiens"]
    ):
        species_match = True
    elif user_input.species.lower() == "mouse" and _match_context(
        combined, ["mouse", "mice", "murine", "mus musculus"]
    ):
        species_match = True

    has_abstract = bool(paper.abstract.strip())
    sentence = _extract_relevant_sentence(gene, paper)
    if not has_abstract:
        sentence = ABSTRACT_UNAVAILABLE

    # When no tissue/disease context was given by the user, treat all papers as
    # matched so they aren't penalised for context we weren't asked to filter on.
    no_tissue_context = not user_input.tissue.strip()
    no_disease_context = not user_input.disease.strip()
    effective_tissue_match = True if no_tissue_context else tissue_match
    effective_disease_match = True if no_disease_context else disease_match

    score = 0.35
    label = "Low"
    if has_abstract and gene.upper() in paper.abstract.upper():
        score = 0.45
        label = "Medium"
    if effective_tissue_match and effective_disease_match and has_abstract:
        score = 0.6
        label = "Medium"
    if effective_tissue_match and has_abstract and ("single-cell" in combined or "scrna" in combined):
        score = min(0.75, score + 0.1)
        label = "High" if score >= 0.75 else label

    predicted_cell_type, source, source_reason = _predicted_cell_type_from_literature(
        gene, paper, marker_result
    )
    reasoning = source_reason
    if source == "literature":
        reasoning += " Cell type derived from PubMed text, not marker rules."
    elif source == "marker_fallback":
        reasoning += " Literature did not name a cell type; marker rules used as fallback."

    return GeneEvidence(
        gene=gene,
        predicted_cell_type=predicted_cell_type,
        evidence_sentence=sentence,
        evidence_type="Abstract" if has_abstract else "Title only",
        pmid=paper.pmid,
        paper_title=paper.title,
        journal=paper.journal,
        year=paper.year,
        pubmed_url=paper.pubmed_url,
        tissue_match=tissue_match,
        disease_match=disease_match,
        species_match=species_match,
        confidence_label=label,
        confidence_score=score,
        reasoning=reasoning,
        literature_inferred=(source == "literature"),
    )


def _extract_evidence_batch_llm(
    gene: str,
    papers: list[PubMedPaper],
    user_input: UserInput,
    marker_result: MarkerAgentResult,
    llm,
) -> list[GeneEvidence]:
    """Optional: one LLM call per gene for up to a few papers."""
    from services.llm_service import LLMServiceBase

    if not isinstance(llm, LLMServiceBase):
        return []

    candidates = [c.cell_type for c in marker_result.candidates[:5]]
    blocks = "\n\n---\n\n".join(_build_paper_context(p) for p in papers)
    prompt = (
        f"Gene: {gene}\n"
        f"Tissue: {user_input.tissue}\n"
        f"Disease: {user_input.disease}\n"
        f"Species: {user_input.species}\n"
        f"Marker candidates: {', '.join(candidates)}\n\n"
        f"Papers:\n{blocks}\n\n"
        "Return one GeneEvidence per paper in evidence_items. "
        "Never invent PMIDs or quotes. Use Insufficient when unsupported."
    )
    try:
        result = llm.complete_json(
            system="Extract PubMed evidence for gene-to-cell-type relationships.",
            user=prompt,
            schema=_GeneEvidenceBatch,
        )
        items = []
        for item in result.evidence_items:
            item.gene = gene
            if item.confidence_label != "Insufficient":
                items.append(item)
        return items
    except Exception as exc:
        logger.warning("Evidence LLM batch extraction failed for %s: %s", gene, exc, exc_info=True)
        return []


def run_evidence_agent(
    user_input: UserInput,
    papers_by_gene: dict[str, list[PubMedPaper]],
    marker_result: MarkerAgentResult,
    llm=None,
    progress_callback=None,
) -> EvidenceAgentResult:
    """
    Run the Evidence Extraction Agent on retrieved PubMed papers.

    Default mode uses fast heuristic extraction (no LLM per paper).
    Set EVIDENCE_USE_LLM=true to LLM-refine top genes (batched, capped).
    """
    evidence_items: list[GeneEvidence] = []
    warnings: list[str] = []
    genes_with_no_evidence: list[str] = []
    seen_keys: set[tuple[str, str]] = set()

    llm_gene_budget = MAX_LLM_GENES if USE_LLM_EVIDENCE and llm else 0
    llm_genes_used = 0

    for gene, papers in papers_by_gene.items():
        if progress_callback:
            progress_callback(gene)

        if not papers:
            genes_with_no_evidence.append(gene)
            continue

        papers = papers[:MAX_PAPERS_PER_GENE]
        gene_items: list[GeneEvidence] = []

        if llm_gene_budget > llm_genes_used and llm:
            llm_items = _extract_evidence_batch_llm(
                gene, papers[:2], user_input, marker_result, llm
            )
            llm_genes_used += 1
            gene_items.extend(llm_items)

        for paper in papers:
            key = (gene, paper.pmid)
            if key in seen_keys:
                continue
            if any(e.pmid == paper.pmid and e.gene == gene for e in gene_items):
                seen_keys.add(key)
                continue
            item = _heuristic_evidence(gene, paper, marker_result, user_input)
            if item:
                gene_items.append(item)
                seen_keys.add(key)

        if gene_items:
            evidence_items.extend(gene_items)
        else:
            genes_with_no_evidence.append(gene)
            warnings.append(f"No direct evidence text found for {gene} in retrieved papers.")

    if not USE_LLM_EVIDENCE:
        warnings.insert(
            0,
            "Evidence extracted via fast heuristic mode (gene mention + literature phrase parsing). "
            "Set EVIDENCE_USE_LLM=true for LLM-refined evidence (slower).",
        )

    return EvidenceAgentResult(
        evidence_items=evidence_items,
        genes_with_no_evidence=genes_with_no_evidence,
        warnings=warnings,
    )
