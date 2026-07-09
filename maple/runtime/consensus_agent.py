"""Consensus Agent — produces the final ConsensusResult.

MAPLE is a literature-discovery tool: show where marker genes are already
annotated to cell types across papers (tissues/diseases come from the papers,
not from user-provided context). Do not steer results toward one disease context.
"""
from __future__ import annotations

import logging

from maple.models import (
    AnalysisInput,
    ExtractionResult,
    CandidateResult,
    DevilsAdvocateResult,
    ConsensusResult,
    ConsensusAlternative,
    Confidence,
    ConfidenceAdjustment,
)

logger = logging.getLogger(__name__)


def _format_candidate_summary(
    candidate_result: CandidateResult,
    extraction_result: ExtractionResult,
    max_candidates: int = 5,
) -> str:
    """Format candidate list for LLM consensus prompt."""
    lines = []
    rows = extraction_result.evidence_rows
    pmid_to_title: dict[str, str] = {r.pmid: r.paper_title for r in rows}

    for i, cand in enumerate(candidate_result.candidate_labels[:max_candidates], 1):
        gene_str = ", ".join(cand.supporting_genes[:8])
        pmid_str = ", ".join(cand.supporting_pmids[:5])
        paper_titles = [pmid_to_title.get(p, "") for p in cand.supporting_pmids[:3]]
        title_str = " | ".join(t[:60] for t in paper_titles if t)
        lines.append(
            f"{i}. '{cand.candidate_label}' "
            f"(score={cand.candidate_score:.3f}, "
            f"papers={cand.supporting_paper_count}, "
            f"specificity={cand.specificity})\n"
            f"   Genes: {gene_str}\n"
            f"   PMIDs: {pmid_str}\n"
            f"   Papers: {title_str}"
        )
    return "\n\n".join(lines) if lines else "No candidates available."


def _score_to_confidence(
    score: float,
    adjustment: ConfidenceAdjustment,
    supporting_paper_count: int,
) -> Confidence:
    """Map candidate_score + DA adjustment to a Confidence level."""
    if score >= 0.7 and supporting_paper_count >= 3:
        base: Confidence = "High"
    elif score >= 0.4 or supporting_paper_count >= 2:
        base = "Medium"
    elif score >= 0.2:
        base = "Low"
    else:
        base = "Insufficient"

    _levels: list[Confidence] = ["Insufficient", "Low", "Medium", "High"]
    idx = _levels.index(base)
    if adjustment == "raise" and idx < len(_levels) - 1:
        return _levels[idx + 1]
    if adjustment == "lower" and idx > 0:
        return _levels[idx - 1]
    return base


def _all_housekeeping(analysis_input: AnalysisInput) -> bool:
    """Return True if every submitted gene is a known housekeeping/nonspecific gene."""
    from maple.runtime.devils_advocate_agent import KNOWN_NONSPECIFIC_GENES
    return bool(analysis_input.markers) and all(
        g.upper() in KNOWN_NONSPECIFIC_GENES for g in analysis_input.markers
    )


def _genes_with_any_evidence(
    rows: list,
    analysis_input: AnalysisInput,
) -> list[str]:
    """All submitted markers that appear in at least one evidence row."""
    found: set[str] = set()
    for row in rows:
        found.update(g.upper() for g in row.matched_user_genes)
    by_upper = {g.upper(): g for g in analysis_input.markers}
    return [by_upper[u] for u in by_upper if u in found]


def _panel_spans_multiple_annotations(
    candidates: list,
    analysis_input: AnalysisInput,
) -> bool:
    """
    True when literature links the marker panel to several distinct cell types
    rather than one coherent population.
    """
    if len(candidates) < 2:
        return False

    strong = [c for c in candidates if c.candidate_score >= 0.3]
    if len(strong) < 2:
        return False

    top = candidates[0]
    second = candidates[1]
    user_genes = {g.upper() for g in analysis_input.markers}
    top_genes = {g.upper() for g in top.supporting_genes}
    second_genes = {g.upper() for g in second.supporting_genes}
    top_fraction = len(top_genes) / max(len(user_genes), 1)
    second_fraction = len(second_genes) / max(len(user_genes), 1)
    top_pmids = set(top.supporting_pmids)
    second_pmids = set(second.supporting_pmids)

    if (
        top_fraction >= 0.95
        and top.supporting_paper_count >= 2
        and second_fraction < top_fraction
    ):
        return False

    if (
        top_genes == second_genes
        and top_pmids
        and top_pmids == second_pmids
        and top_fraction >= 0.5
    ):
        return False

    # For multi-marker panels, prefer a coherent label that explains a large
    # fraction of the submitted genes over scattered single-gene alternatives.
    if top_fraction >= 0.5:
        if second_fraction <= 0.34 and second.supporting_paper_count <= top.supporting_paper_count:
            return False
        if top.candidate_score >= second.candidate_score + 0.15:
            return False
        if (
            top_fraction >= 0.95
            and second_fraction >= 0.95
            and getattr(top, "specificity", "") == "specific"
            and top.supporting_paper_count >= second.supporting_paper_count + 2
        ):
            return False

    if second.candidate_score >= top.candidate_score * 0.65:
        return True

    all_genes: set[str] = set()
    for cand in candidates[:6]:
        all_genes.update(g.upper() for g in cand.supporting_genes)

    if len(top_genes) < max(2, len(user_genes) // 2) and len(strong) >= 2:
        return True
    if len(all_genes) >= 3 and len(top_genes) < len(all_genes) * 0.55:
        return True
    return False


def _discovery_rationale(candidates: list, rows: list) -> str:
    """Summarize distinct literature annotations for the marker panel."""
    pmid_to_title = {r.pmid: r.paper_title for r in rows}
    parts: list[str] = []
    for cand in candidates[:6]:
        if cand.candidate_score < 0.25:
            continue
        title = pmid_to_title.get(cand.supporting_pmids[0], "")[:70] if cand.supporting_pmids else ""
        parts.append(
            f"{cand.candidate_label} "
            f"({cand.supporting_paper_count} paper(s), "
            f"genes: {', '.join(cand.supporting_genes[:5])}"
            f"{'; e.g. ' + title if title else ''})"
        )
    if not parts:
        return "See the evidence table for per-paper cell-type associations."
    return (
        "Markers in this panel are reported for multiple cell populations across "
        "published studies. Tissue and disease context differ by paper — use the "
        "evidence table to review each association. Literature annotations include: "
        + "; ".join(parts[:5])
        + "."
    )


def _build_alternatives(candidates: list, skip_normalized: str | None = None) -> list[ConsensusAlternative]:
    """Build alternative label list from ranked candidates."""
    alts: list[ConsensusAlternative] = []
    for cand in candidates:
        if skip_normalized and cand.normalized_label == skip_normalized:
            continue
        if len(alts) >= 5:
            break
        alts.append(
            ConsensusAlternative(
                label=cand.candidate_label,
                reason_not_selected=(
                    f"Literature score {cand.candidate_score:.3f}, "
                    f"{cand.supporting_paper_count} paper(s)"
                ),
                supporting_genes=cand.supporting_genes[:8],
                supporting_pmids=cand.supporting_pmids[:5],
            )
        )
    return alts


def _coherent_top_candidate(candidates: list, analysis_input: AnalysisInput):
    """Return the top candidate when deterministic evidence supports one label."""
    if not candidates or _panel_spans_multiple_annotations(candidates, analysis_input):
        return None
    top = candidates[0]
    user_genes = {g.upper() for g in analysis_input.markers}
    top_genes = {g.upper() for g in top.supporting_genes}
    gene_fraction = len(top_genes) / max(len(user_genes), 1)
    if top.candidate_score >= 0.55 and (gene_fraction >= 0.5 or top.supporting_paper_count >= 2):
        return top
    if top.candidate_score >= 0.75:
        return top
    return None


def _supporting_papers_for_pmids(rows: list, pmids: list[str], max_items: int = 5) -> list[str]:
    """Short PMID:title strings for selected supporting papers."""
    pmid_to_title = {r.pmid: r.paper_title for r in rows}
    return [
        f"{pmid}: {pmid_to_title.get(pmid, 'Unknown title')[:80]}"
        for pmid in pmids[:max_items]
    ]


def _heuristic_consensus(
    analysis_input: AnalysisInput,
    extraction_result: ExtractionResult,
    candidate_result: CandidateResult,
    devils_advocate: DevilsAdvocateResult,
) -> ConsensusResult:
    """Heuristic consensus when no LLM is available."""
    rows = extraction_result.evidence_rows
    candidates = candidate_result.candidate_labels

    if _all_housekeeping(analysis_input):
        return ConsensusResult(
            consensus_label="Insufficient evidence",
            confidence="Insufficient",
            consensus_rationale=(
                "All submitted genes are non-specific housekeeping genes "
                "(e.g. ACTB, GAPDH, MALAT1) that are expressed in virtually every cell type. "
                "No cell-type-specific annotation is possible from these markers alone."
            ),
            devils_advocate_critique=devils_advocate.critique_summary,
            what_would_improve_confidence=[
                "Replace housekeeping genes with cell-type-specific markers",
            ],
            audit_notes=["Heuristic consensus: all genes are housekeeping/nonspecific"],
        )

    if not candidates:
        return ConsensusResult(
            consensus_label="Insufficient evidence",
            confidence="Insufficient",
            consensus_rationale="No candidate cell types could be extracted from the retrieved papers.",
            devils_advocate_critique=devils_advocate.critique_summary,
            what_would_improve_confidence=[
                "Try additional marker genes with known cell-type specificity",
            ],
            audit_notes=["Heuristic consensus: no candidates"],
        )

    supporting_genes = _genes_with_any_evidence(rows, analysis_input)
    multi = _panel_spans_multiple_annotations(candidates, analysis_input)

    if multi:
        label = "Multiple cell types in literature"
        rationale = _discovery_rationale(candidates, rows)
        confidence: Confidence = "Medium" if len(candidates) >= 3 else "Low"
        alternatives = _build_alternatives(candidates[1:], skip_normalized=None)
        main_pmids = list(dict.fromkeys(
            pmid for c in candidates[:4] for pmid in c.supporting_pmids
        ))[:10]
    else:
        top = candidates[0]
        label = top.candidate_label
        rationale = (
            f"Strongest single literature association: '{top.candidate_label}' "
            f"({top.supporting_paper_count} paper(s), genes: "
            f"{', '.join(top.supporting_genes[:8])}). "
            + _discovery_rationale(candidates[1:3], rows)
            if len(candidates) > 1
            else f"Supported by {top.supporting_paper_count} paper(s)."
        )
        adjustment = devils_advocate.recommended_confidence_adjustment
        confidence = _score_to_confidence(
            top.candidate_score, adjustment, top.supporting_paper_count
        )
        alternatives = _build_alternatives(candidates[1:])
        main_pmids = top.supporting_pmids[:10]

    supporting_papers = _supporting_papers_for_pmids(rows, main_pmids)

    what_to_improve: list[str] = []
    if multi:
        what_to_improve.append(
            "Compare evidence rows by paper title/abstract to see tissue and disease context"
        )
    if devils_advocate.additional_markers_needed:
        what_to_improve.extend(devils_advocate.additional_markers_needed[:2])

    return ConsensusResult(
        consensus_label=label,
        confidence=confidence,
        consensus_rationale=rationale,
        supporting_genes=supporting_genes,
        main_supporting_pmids=main_pmids,
        main_supporting_papers=supporting_papers,
        alternative_labels=alternatives,
        devils_advocate_critique=devils_advocate.critique_summary,
        what_would_improve_confidence=what_to_improve[:5],
        audit_notes=["Heuristic consensus (discovery mode, no LLM)"],
    )


def run_consensus_agent(
    analysis_input: AnalysisInput,
    extraction_result: ExtractionResult,
    candidate_result: CandidateResult,
    devils_advocate: DevilsAdvocateResult,
    llm=None,
) -> ConsensusResult:
    """Produce the final ConsensusResult."""
    if llm is not None:
        try:
            return _llm_consensus(
                analysis_input,
                extraction_result,
                candidate_result,
                devils_advocate,
                llm,
            )
        except Exception as exc:
            logger.warning("LLM consensus failed: %s — using heuristic fallback", exc)

    return _heuristic_consensus(
        analysis_input, extraction_result, candidate_result, devils_advocate
    )


def _llm_consensus(
    analysis_input: AnalysisInput,
    extraction_result: ExtractionResult,
    candidate_result: CandidateResult,
    devils_advocate: DevilsAdvocateResult,
    llm,
) -> ConsensusResult:
    """Run consensus using LLM, then apply discovery-mode post-processing."""
    from maple import config as cfg
    from maple.extraction.prompts import CONSENSUS_SYSTEM, CONSENSUS_USER_TEMPLATE
    from maple.extraction.schemas import _LLMCandidateConsensus

    candidate_summary = _format_candidate_summary(
        candidate_result, extraction_result, max_candidates=8
    )

    user_prompt = CONSENSUS_USER_TEMPLATE.format(
        genes=", ".join(analysis_input.markers),
        n_rows=len(extraction_result.evidence_rows),
        candidate_summary=candidate_summary,
        critique_summary=devils_advocate.critique_summary,
        strongest_counterargument=devils_advocate.strongest_counterargument,
        adjustment=devils_advocate.recommended_confidence_adjustment,
        context_note=(
            "User focused the search on: "
            + ", ".join(
                f"{k}={v}" for k, v in (
                    ("tissue", analysis_input.tissue),
                    ("disease", analysis_input.disease),
                    ("species", analysis_input.species),
                ) if v
            )
            + ". Retrieval and ranking were biased toward this context."
            if analysis_input.has_context
            else "No user tissue/disease context was applied to retrieval or scoring."
        ),
    )

    raw = llm.complete_json(
        system=CONSENSUS_SYSTEM,
        user=user_prompt,
        schema=_LLMCandidateConsensus,
    )

    raw_conf = (raw.confidence or "Insufficient").strip().capitalize()
    valid_confidences = {"High", "Medium", "Low", "Insufficient"}
    confidence: Confidence = raw_conf if raw_conf in valid_confidences else "Insufficient"  # type: ignore[assignment]

    alternative_labels: list[ConsensusAlternative] = []
    for alt in raw.alternative_labels:
        if isinstance(alt, dict):
            alternative_labels.append(
                ConsensusAlternative(
                    label=alt.get("label", ""),
                    reason_not_selected=alt.get("reason_not_selected", alt.get("reason", "")),
                    supporting_genes=alt.get("supporting_genes", []),
                    supporting_pmids=alt.get("supporting_pmids", []),
                )
            )

    candidates = candidate_result.candidate_labels
    supporting_genes = _genes_with_any_evidence(extraction_result.evidence_rows, analysis_input)
    coherent_top = _coherent_top_candidate(candidates, analysis_input)
    confidence_adjustment_applied = False

    if _panel_spans_multiple_annotations(candidates, analysis_input):
        consensus_label = "Multiple cell types in literature"
        consensus_rationale = _discovery_rationale(candidates, extraction_result.evidence_rows)
        if confidence == "High":
            confidence = "Medium"
        if not alternative_labels:
            alternative_labels = _build_alternatives(candidates)
        main_pmids = list(dict.fromkeys(
            pmid for c in candidates[:5] for pmid in c.supporting_pmids
        ))[:10]
        main_papers = raw.main_supporting_papers or []
    elif coherent_top is not None:
        consensus_label = coherent_top.candidate_label
        consensus_rationale = (
            f"Deterministic evidence ranking selected '{coherent_top.candidate_label}' "
            f"because it is the strongest coherent literature association "
            f"(score={coherent_top.candidate_score:.3f}, "
            f"{coherent_top.supporting_paper_count} paper(s), genes: "
            f"{', '.join(coherent_top.supporting_genes[:8])}). "
            "Other retrieved annotations are lower-coverage alternatives."
        )
        confidence = _score_to_confidence(
            coherent_top.candidate_score,
            devils_advocate.recommended_confidence_adjustment,
            coherent_top.supporting_paper_count,
        )
        confidence_adjustment_applied = True
        alternative_labels = _build_alternatives(
            candidates[1:], skip_normalized=coherent_top.normalized_label
        )
        main_pmids = coherent_top.supporting_pmids[:10]
        main_papers = _supporting_papers_for_pmids(
            extraction_result.evidence_rows, main_pmids
        )
    else:
        consensus_label = raw.consensus_label or "Insufficient evidence"
        consensus_rationale = raw.consensus_rationale or _discovery_rationale(candidates, extraction_result.evidence_rows)
        main_pmids = raw.main_supporting_pmids
        main_papers = raw.main_supporting_papers

    if devils_advocate.recommended_confidence_adjustment == "lower" and not confidence_adjustment_applied:
        _levels: list[Confidence] = ["Insufficient", "Low", "Medium", "High"]
        idx = _levels.index(confidence) if confidence in _levels else 0
        if idx > 0:
            confidence = _levels[idx - 1]

    return ConsensusResult(
        consensus_label=consensus_label,
        confidence=confidence,
        consensus_rationale=consensus_rationale,
        supporting_genes=supporting_genes,
        main_supporting_pmids=main_pmids,
        main_supporting_papers=main_papers,
        alternative_labels=alternative_labels or _build_alternatives(candidates[1:]),
        devils_advocate_critique=raw.devils_advocate_critique or devils_advocate.critique_summary,
        what_would_improve_confidence=raw.what_would_improve_confidence or [
            "Review the evidence table for tissue/disease context per paper",
        ],
        audit_notes=["LLM-based consensus (discovery mode)"],
    )
