"""Devil's Advocate Agent — challenges the leading candidate label.

Never imports marker_agent.py or marker_rules.json.
"""
from __future__ import annotations

import logging
from typing import Optional

from maple.models import (
    AnalysisInput,
    ExtractionResult,
    CandidateResult,
    DevilsAdvocateResult,
    AlternativeLabel,
    ConfidenceAdjustment,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known housekeeping / non-specific genes that appear in most cell types.
# Using them as "markers" is uninformative.
# ---------------------------------------------------------------------------
KNOWN_NONSPECIFIC_GENES: frozenset[str] = frozenset({
    "ACTB", "GAPDH", "MALAT1", "B2M", "ACTG1",
    "HSP90AB1", "HSPA8", "ENO1", "TPI1", "PGK1",
    "LDHA", "PKM", "ALDOA", "GPI", "PFKL", "ATP5A1",
    "RPL13A", "RPS18", "TUBB", "TUBA1B", "VIM",
    "EEF1A1", "EEF2", "EIF4A1", "HNRNPA1",
})


def _format_evidence_summary(extraction_result: ExtractionResult, max_rows: int = 10) -> str:
    """Format top evidence rows for inclusion in LLM prompt."""
    rows = extraction_result.evidence_rows[:max_rows]
    lines = []
    for i, row in enumerate(rows, 1):
        genes_str = ", ".join(row.matched_user_genes[:5])
        lines.append(
            f"{i}. PMID {row.pmid} | Cell type: '{row.celltype_label}' | "
            f"Genes: {genes_str} | Strength: {row.match_strength} | "
            f"Snippet: {row.evidence_snippet[:120]}"
        )
    return "\n".join(lines) if lines else "No evidence rows available."


def _heuristic_devils_advocate(
    analysis_input: AnalysisInput,
    extraction_result: ExtractionResult,
    candidate_result: CandidateResult,
) -> DevilsAdvocateResult:
    """
    Heuristic Devil's Advocate when no LLM is available.

    Flags:
    - Broad top-candidate label
    - Non-specific / housekeeping genes in the input
    - Fewer than 2 supporting papers
    - Abstract-only evidence (no full-text rows)
    - Context mismatches
    """
    issues: list[str] = []
    adjustment: ConfidenceAdjustment = "keep"
    alternative_labels: list[AlternativeLabel] = []

    top_candidate = (
        candidate_result.candidate_labels[0] if candidate_result.candidate_labels else None
    )

    # 1. Broad label check (specificity is the LLM's judgement, not a hardcoded list)
    if top_candidate and top_candidate.specificity == "broad":
        issues.append(
            f"Top candidate '{top_candidate.candidate_label}' is a broad lineage label, "
            "not a specific cell subtype. More specific annotation may be possible."
        )
        adjustment = "lower"

    # 2. Non-specific genes
    nonspecific = [g for g in analysis_input.markers if g.upper() in KNOWN_NONSPECIFIC_GENES]
    if nonspecific:
        issues.append(
            f"Gene(s) {', '.join(nonspecific)} are non-specific housekeeping genes "
            "present in virtually all cell types and should not drive annotation."
        )
        if adjustment == "keep":
            adjustment = "lower"

    # 3. Paper count
    if top_candidate and top_candidate.supporting_paper_count < 2:
        issues.append(
            f"Top candidate supported by only {top_candidate.supporting_paper_count} paper(s). "
            "Multiple independent papers are recommended for confident annotation."
        )
        if adjustment == "keep":
            adjustment = "lower"

    # 4. Abstract-only evidence
    rows = extraction_result.evidence_rows
    if rows:
        fulltext_rows = [
            r for r in rows
            if r.evidence_section in ("results", "figure", "table", "supplement")
        ]
        if not fulltext_rows:
            issues.append(
                "All evidence comes from abstracts only. Full-text evidence (results/figures) "
                "would provide stronger support."
            )

    # 5. Conflicting evidence (multiple distinct top-tier candidates with similar scores)
    conflicting: list[str] = []
    if len(candidate_result.candidate_labels) >= 2:
        top_score = candidate_result.candidate_labels[0].candidate_score
        second = candidate_result.candidate_labels[1]
        if second.candidate_score >= 0.7 * top_score:
            conflicting.append(
                f"Alternative candidate '{second.candidate_label}' "
                f"(score={second.candidate_score:.3f}) is nearly as strong as the top candidate."
            )
            alternative_labels.append(
                AlternativeLabel(
                    label=second.candidate_label,
                    reason=f"Score {second.candidate_score:.3f} vs top {top_score:.3f}",
                    supporting_genes=second.supporting_genes[:5],
                    supporting_pmids=second.supporting_pmids[:3],
                )
            )

    # 6. Context mismatches from evidence rows
    context_mismatches: list[str] = []
    mismatch_count = sum(
        1 for r in rows
        if r.context_match.tissue == "mismatched" or r.context_match.disease == "mismatched"
    )
    if mismatch_count > 0:
        context_mismatches.append(
            f"{mismatch_count} evidence row(s) have tissue/disease context mismatches."
        )
        if adjustment == "keep":
            adjustment = "lower"

    critique = (
        "; ".join(issues)
        if issues
        else "No major concerns identified in heuristic review."
    )
    strongest = issues[0] if issues else "Evidence appears consistent with top candidate."

    additional_markers: list[str] = []
    if nonspecific and top_candidate:
        additional_markers.append(
            "Add tissue/lineage-specific markers beyond housekeeping genes"
        )

    return DevilsAdvocateResult(
        critique_summary=critique,
        strongest_counterargument=strongest,
        possible_alternative_labels=alternative_labels,
        nonspecific_genes=nonspecific,
        conflicting_evidence=conflicting,
        context_mismatches=context_mismatches,
        recommended_confidence_adjustment=adjustment,
        confidence_adjustment_reason=", ".join(issues[:2]) if issues else "No major issues",
        additional_markers_needed=additional_markers,
        additional_context_needed=(
            ["Provide tissue context for more targeted evidence retrieval"]
            if not analysis_input.tissue
            else []
        ),
        audit_notes=["Heuristic Devil's Advocate (no LLM)"],
    )


def run_devils_advocate_agent(
    analysis_input: AnalysisInput,
    extraction_result: ExtractionResult,
    candidate_result: CandidateResult,
    llm=None,
) -> DevilsAdvocateResult:
    """
    Run the Devil's Advocate agent.

    Uses LLM when available; falls back to heuristic approach.
    """
    top_candidate = (
        candidate_result.candidate_labels[0] if candidate_result.candidate_labels else None
    )

    if not top_candidate:
        return DevilsAdvocateResult(
            critique_summary="No candidates to challenge — insufficient evidence.",
            strongest_counterargument="No evidence rows were extracted from the retrieved papers.",
            recommended_confidence_adjustment="lower",
            confidence_adjustment_reason="No evidence rows available",
            audit_notes=["Devil's Advocate: no candidates"],
        )

    if llm is not None:
        try:
            return _llm_devils_advocate(
                analysis_input, extraction_result, candidate_result, top_candidate, llm
            )
        except Exception as exc:
            logger.warning("LLM Devil's Advocate failed: %s — using heuristic fallback", exc)

    return _heuristic_devils_advocate(analysis_input, extraction_result, candidate_result)


def _llm_devils_advocate(
    analysis_input: AnalysisInput,
    extraction_result: ExtractionResult,
    candidate_result: CandidateResult,
    top_candidate,
    llm,
) -> DevilsAdvocateResult:
    """Run Devil's Advocate using LLM."""
    from maple.extraction.prompts import DEVILS_ADVOCATE_SYSTEM, DEVILS_ADVOCATE_USER_TEMPLATE

    evidence_summary = _format_evidence_summary(extraction_result, max_rows=10)

    user_prompt = DEVILS_ADVOCATE_USER_TEMPLATE.format(
        top_candidate_label=top_candidate.candidate_label,
        top_score=top_candidate.candidate_score,
        genes=", ".join(analysis_input.markers),
        n_rows=len(extraction_result.evidence_rows),
        evidence_summary=evidence_summary,
    )

    result = llm.complete_json(
        system=DEVILS_ADVOCATE_SYSTEM,
        user=user_prompt,
        schema=DevilsAdvocateResult,
    )

    # Ensure nonspecific_genes from KNOWN_NONSPECIFIC_GENES are flagged even if LLM missed them
    known_nonspecific = [g for g in analysis_input.markers if g.upper() in KNOWN_NONSPECIFIC_GENES]
    if known_nonspecific:
        existing = {g.upper() for g in result.nonspecific_genes}
        for g in known_nonspecific:
            if g.upper() not in existing:
                result.nonspecific_genes.append(g)

    result.audit_notes = result.audit_notes or []
    result.audit_notes.append("LLM-based Devil's Advocate")
    return result
