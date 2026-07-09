"""Evidence Extraction Agent — LLM-first marker-to-celltype extraction.

The LLM reads each retrieved paper and decides whether the user's marker genes
are described as markers of a specific cell type. There are NO hardcoded cell
types, tissue priors, or label blocklists: the cell-type label, its specificity,
and the evidence type all come from the paper text as judged by the LLM.

Only generic, source-grounding validation is applied (genes must be a subset of
the user's panel, the snippet must be traceable to the paper text, and the genes
must appear in the snippet) so that citations stay faithful to the literature.

CRITICAL: Cell type labels MUST come from retrieved paper text only.
Never import marker_agent.py or marker_rules.json.
"""
from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from maple.models import (
    AnalysisInput,
    RetrievalResult,
    RetrievedPaper,
    EvidenceRow,
    ExtractionResult,
    ContextMatchDetail,
    EvidenceSection,
    EvidenceType,
    MatchStrength,
    CandidateSpecificity,
)
from maple import config as cfg
from maple.extraction.validators import (
    validate_quote_fragments,
    validate_genes_in_text,
    validate_genes_in_snippet,
)

logger = logging.getLogger(__name__)

_ALLOWED_EVIDENCE_TYPES: frozenset[str] = frozenset({
    "direct_marker_celltype_assignment",
    "cluster_annotation",
    "differential_expression_only",
    "gene_mention_only",
})
_ALLOWED_SECTIONS: frozenset[str] = frozenset({
    "abstract", "results", "figure", "table", "supplement", "unknown",
})
_ALLOWED_SPECIFICITY: frozenset[str] = frozenset({"specific", "intermediate", "broad"})
_ALLOWED_STRENGTH: frozenset[str] = frozenset({"High", "Medium", "Low"})


def _paper_text(paper: RetrievedPaper) -> str:
    """Combined searchable text for a paper (title + abstract + full_text)."""
    parts = [paper.title or "", paper.abstract or "", paper.full_text or ""]
    return " ".join(p for p in parts if p)


def _find_user_genes_in_text(user_genes: list[str], text: str) -> list[str]:
    return validate_genes_in_text(user_genes, text)


def _build_context_match(
    paper: RetrievedPaper,
    analysis_input: AnalysisInput,
) -> ContextMatchDetail:
    """Record whether the paper text mentions any user-provided context terms."""
    text_lower = _paper_text(paper).lower()

    def _check(user_val: Optional[str]) -> str:
        if not user_val:
            return "not_provided"
        keywords = [w for w in user_val.lower().split() if len(w) > 3]
        if not keywords:
            return "unknown"
        matches = sum(1 for kw in keywords if kw in text_lower)
        return "matched" if matches >= max(1, len(keywords) // 2) else "unknown"

    return ContextMatchDetail(
        species=_check(analysis_input.species),
        tissue=_check(analysis_input.tissue),
        disease=_check(analysis_input.disease),
        technology=_check(analysis_input.technology),
    )


def _label_grounded_in_text(label: str, paper_text: str) -> bool:
    """Require the label's distinctive words to appear in the paper text."""
    generic = {"cell", "cells", "type", "types", "like", "state", "line", "lines", "the", "a"}
    tokens = [w for w in re.findall(r"[a-z0-9+]+", label.lower()) if len(w) > 2 and w not in generic]
    if not tokens:
        # Label was purely generic (e.g. "cells") — accept, snippet already validated.
        return True
    text_lower = paper_text.lower()
    hits = sum(1 for t in tokens if t in text_lower)
    return hits >= max(1, len(tokens) // 2)


def _normalize_enum(value: str, allowed: frozenset[str], default: str) -> str:
    v = (value or "").strip()
    if v in allowed:
        return v
    lowered = v.lower()
    for a in allowed:
        if a.lower() == lowered:
            return a
    return default


def _llm_extract(
    paper: RetrievedPaper,
    user_genes: list[str],
    analysis_input: AnalysisInput,
    llm,
) -> list[EvidenceRow]:
    """Extract evidence rows from a paper using the LLM. Returns [] on failure."""
    from maple.extraction.prompts import (
        EVIDENCE_EXTRACTION_SYSTEM,
        EVIDENCE_EXTRACTION_USER_TEMPLATE,
        EVIDENCE_EXTRACTION_FULLTEXT_PLACEHOLDER,
    )
    from maple.extraction.schemas import _LLMEvidenceOutput

    paper_text = _paper_text(paper)
    found_genes = _find_user_genes_in_text(user_genes, paper_text)
    if not found_genes:
        return []

    fulltext_section = ""
    if paper.full_text:
        fulltext_section = EVIDENCE_EXTRACTION_FULLTEXT_PLACEHOLDER.format(
            full_text=paper.full_text[: cfg.LLM_FULLTEXT_CHARS]
        )

    user_prompt = EVIDENCE_EXTRACTION_USER_TEMPLATE.format(
        pmid=paper.pmid,
        title=paper.title or "",
        abstract=(paper.abstract or "")[:3000],
        fulltext_section=fulltext_section,
        genes=", ".join(user_genes),
    )

    try:
        output = llm.complete_json(
            system=EVIDENCE_EXTRACTION_SYSTEM,
            user=user_prompt,
            schema=_LLMEvidenceOutput,
        )
    except Exception as exc:
        logger.warning("LLM extraction failed for PMID %s: %s", paper.pmid, exc)
        return []

    user_upper = {u.upper(): u for u in user_genes}
    context = _build_context_match(paper, analysis_input)
    rows: list[EvidenceRow] = []

    for raw in output.rows:
        label = (raw.celltype_label or "").strip()
        snippet = (raw.evidence_snippet or "").strip()[:800]
        if not label or not snippet:
            continue

        # Genes must be a subset of the user's panel.
        valid_genes = [user_upper[g.upper()] for g in raw.matched_user_genes if g.upper() in user_upper]
        valid_genes = list(dict.fromkeys(valid_genes))
        if not valid_genes:
            continue

        # Source grounding: snippet must be an exact (searchable) quote from the
        # paper, and the label must be named in the paper text.
        if not validate_quote_fragments(snippet, paper_text):
            continue
        if not _label_grounded_in_text(label, paper_text):
            continue

        # Keep only the genes that literally appear in the quoted passage, rather
        # than rejecting the whole row — this preserves a multi-sentence marker
        # panel while dropping any gene the quote does not actually support.
        valid_genes = [g for g in valid_genes if validate_genes_in_snippet([g], snippet)]
        if not valid_genes:
            continue

        section: EvidenceSection = _normalize_enum(  # type: ignore[assignment]
            raw.evidence_section, _ALLOWED_SECTIONS, "unknown"
        )
        evidence_type: EvidenceType = _normalize_enum(  # type: ignore[assignment]
            raw.evidence_type, _ALLOWED_EVIDENCE_TYPES, "direct_marker_celltype_assignment"
        )
        specificity: CandidateSpecificity = _normalize_enum(  # type: ignore[assignment]
            raw.specificity, _ALLOWED_SPECIFICITY, "intermediate"
        )
        strength: MatchStrength = _normalize_enum(  # type: ignore[assignment]
            raw.match_strength, _ALLOWED_STRENGTH, "Medium" if len(valid_genes) >= 2 else "Low"
        )
        normalized = (raw.normalized_label or label).strip().lower()

        def _clean_ctx(val: str) -> Optional[str]:
            v = (val or "").strip()
            if not v or v.lower() in {"n/a", "na", "none", "not stated", "unknown", "unspecified"}:
                return None
            return v[:80]

        rows.append(
            EvidenceRow(
                pmid=paper.pmid,
                pmcid=paper.pmcid,
                paper_title=paper.title or "",
                journal=paper.journal,
                publication_date=paper.publication_date,
                publication_year=paper.publication_year,
                celltype_label=label,
                normalized_label=normalized,
                marker_specific=bool(raw.marker_specific),
                specificity=specificity,
                matched_user_genes=valid_genes,
                number_of_user_genes_found=len(valid_genes),
                evidence_snippet=snippet[:800],
                evidence_section=section,
                match_strength=strength,
                evidence_type=evidence_type,
                match_reason=(
                    f"LLM ({evidence_type}): {len(valid_genes)} gene(s) with '{label}'"
                ),
                context_match=context,
                tissue=_clean_ctx(getattr(raw, "tissue", "")),
                disease=_clean_ctx(getattr(raw, "disease", "")),
                species=_clean_ctx(getattr(raw, "species", "")),
                source_url=paper.source_url,
            )
        )

    return _dedupe_rows(rows)


def _dedupe_rows(rows: list[EvidenceRow]) -> list[EvidenceRow]:
    """Keep the strongest row per (pmid, normalized label)."""
    best: dict[tuple[str, str], EvidenceRow] = {}
    for row in rows:
        key = (row.pmid, row.normalized_label or row.celltype_label.lower())
        cur = best.get(key)
        if cur is None or row.number_of_user_genes_found > cur.number_of_user_genes_found:
            best[key] = row
    return list(best.values())


def run_evidence_agent(
    analysis_input: AnalysisInput,
    retrieval_result: RetrievalResult,
    llm=None,
) -> ExtractionResult:
    """Extract EvidenceRow objects from retrieved papers using the LLM."""
    user_genes = analysis_input.markers
    papers = retrieval_result.retrieved_papers
    audit_notes: list[str] = []
    excluded_reasons: list[str] = []
    excluded_count = 0
    all_rows: list[EvidenceRow] = []

    use_llm = llm is not None and cfg.ENABLE_LLM_EXTRACTION
    llm_budget = cfg.LLM_EXTRACTION_MAX_PAPERS if use_llm else 0
    llm_used = 0

    abstract_only = 0
    papers_direct = papers_cluster = papers_de_only = papers_gene_only = 0
    papers_no_assignment = 0

    if not use_llm:
        audit_notes.append(
            "LLM extraction unavailable — no evidence extracted. "
            "Provide an API key so the model can read the papers."
        )
        return ExtractionResult(
            evidence_rows=[],
            excluded_paper_count=len(papers),
            excluded_reasons=[
                f"PMID {p.pmid}: skipped (LLM extractor disabled)" for p in papers[:20]
            ],
            audit_notes=audit_notes,
            abstract_only_papers=sum(1 for p in papers if not p.full_text),
        )

    abstract_only = sum(1 for p in papers if not p.full_text)

    # ── Pass 1: select papers eligible for an LLM call (genes present, in budget) ──
    eligible: list[RetrievedPaper] = []
    eligible_found: list[list[str]] = []
    for paper in papers:
        found_genes = _find_user_genes_in_text(user_genes, _paper_text(paper))
        if not found_genes:
            excluded_count += 1
            excluded_reasons.append(
                f"PMID {paper.pmid}: no user-provided genes found in title/abstract/full_text"
            )
            continue
        if len(eligible) >= llm_budget:
            excluded_count += 1
            excluded_reasons.append(
                f"PMID {paper.pmid}: genes found but LLM budget "
                f"({llm_budget} papers) exhausted"
            )
            continue
        eligible.append(paper)
        eligible_found.append(found_genes)

    # ── Pass 2: run per-paper extraction concurrently (bounded thread pool) ──────
    results: dict[int, list[EvidenceRow]] = {}
    if eligible:
        max_workers = max(1, min(len(eligible), cfg.EVIDENCE_EXTRACTION_CONCURRENCY))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(_llm_extract, paper, user_genes, analysis_input, llm): idx
                for idx, paper in enumerate(eligible)
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    logger.warning(
                        "LLM extraction error for PMID %s: %s", eligible[idx].pmid, exc
                    )
                    results[idx] = []
    llm_used = len(eligible)

    # ── Pass 3: aggregate in stable paper order ──────────────────────────────────
    for idx, paper in enumerate(eligible):
        paper_rows = results.get(idx, [])
        if not paper_rows:
            papers_no_assignment += 1
            excluded_count += 1
            excluded_reasons.append(
                f"PMID {paper.pmid}: genes found ({', '.join(eligible_found[idx])}) "
                "but the model found no explicit cell-type assignment"
            )
            continue

        # Per-paper evidence-type tally uses the strongest row.
        best_type = max(
            (r.evidence_type for r in paper_rows),
            key=lambda t: _evidence_type_rank(t),
        )
        if best_type == "direct_marker_celltype_assignment":
            papers_direct += 1
        elif best_type == "cluster_annotation":
            papers_cluster += 1
        elif best_type == "differential_expression_only":
            papers_de_only += 1
        else:
            papers_gene_only += 1

        all_rows.extend(paper_rows)

    audit_notes.append(
        f"Processed {len(papers)} papers with the LLM extractor "
        f"({llm_used} model calls): {len(all_rows)} evidence rows, "
        f"{excluded_count} papers excluded"
    )
    audit_notes.append(
        f"Evidence signals: direct assignment={papers_direct}, cluster={papers_cluster}, "
        f"DE-only={papers_de_only}, gene-mention-only={papers_gene_only}, "
        f"no assignment={papers_no_assignment}"
    )

    # Sort by evidence type, gene count, then match strength — all LLM-derived.
    _strength_order = {"High": 3, "Medium": 2, "Low": 1}
    all_rows.sort(
        key=lambda r: (
            _evidence_type_rank(r.evidence_type),
            r.number_of_user_genes_found,
            _strength_order.get(r.match_strength, 0),
        ),
        reverse=True,
    )

    return ExtractionResult(
        evidence_rows=all_rows,
        excluded_paper_count=excluded_count,
        excluded_reasons=excluded_reasons[:20],
        audit_notes=audit_notes,
        papers_with_direct_assignment=papers_direct,
        papers_with_cluster_annotation=papers_cluster,
        papers_de_only=papers_de_only,
        papers_gene_mention_only=papers_gene_only,
        papers_no_celltype_assignment=papers_no_assignment,
        abstract_only_papers=abstract_only,
        validation_rejected_rows=0,
    )


def _evidence_type_rank(evidence_type: str) -> int:
    return {
        "direct_marker_celltype_assignment": 5,
        "cluster_annotation": 4,
        "differential_expression_only": 2,
        "gene_mention_only": 1,
    }.get(evidence_type, 0)
