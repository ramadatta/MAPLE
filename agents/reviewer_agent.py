"""Reviewer Agent — strict single-cell reviewer critique."""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from models.schemas import (
    EvidenceAgentResult,
    MarkerAgentResult,
    ReviewerComment,
    ReviewerResult,
    UserInput,
)
from services.llm_service import LLMService
from utils.gene_aliases import AT2_MARKERS

FIBROBLAST_CORE = {"COL1A1", "COL3A1", "DCN", "LUM", "PDGFRA"}
ACTIVATED_MARKERS = {"POSTN", "CTHRC1", "FN1", "THBS2"}
MYOFIBROBLAST_MARKERS = {"ACTA2", "TAGLN", "MYH11", "CNN1", "TPM2"}
EPITHELIAL_MARKERS = {"EPCAM", "KRT8", "KRT18", "KRT19", "KRT5", "KRT14", "TP63"}
COLLAGEN_ONLY = {"COL1A1", "COL3A1", "COL6A1", "COL6A2"}


class _ReviewerLLMOutput(BaseModel):
    """LLM output for reviewer narrative fields."""

    reviewer_summary: str = ""
    alternative_annotations: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)


def run_deterministic_reviewer_checks(
    user_input: UserInput,
    marker_result: MarkerAgentResult,
    evidence_result: EvidenceAgentResult,
) -> ReviewerResult:
    """
    Pure deterministic reviewer checks (testable without LLM).

    Returns partial ReviewerResult with caveats and confidence adjustment.
    """
    gene_set = {g.upper() for g in user_input.genes}
    caveats: list[str] = []
    missing_markers: list[str] = []
    alternatives: list[str] = []
    contradictory: list[str] = []
    comments: list[ReviewerComment] = []
    adjustment = 0.0

    stromal = gene_set & (FIBROBLAST_CORE | ACTIVATED_MARKERS | COLLAGEN_ONLY)
    epithelial = gene_set & EPITHELIAL_MARKERS

    if stromal and epithelial:
        msg = (
            "Fibroblast/stromal and epithelial markers co-present — "
            "possible doublet, ambient RNA, or mixed cluster."
        )
        caveats.append(msg)
        comments.append(ReviewerComment(category="contamination", comment=msg, severity="critical"))
        adjustment -= 0.15

    if "PTPRC" in gene_set and stromal:
        msg = "PTPRC with stromal markers — possible mixed identity or doublet."
        caveats.append(msg)
        comments.append(ReviewerComment(category="mixed_identity", comment=msg, severity="warning"))
        adjustment -= 0.1

    collagen_present = gene_set & COLLAGEN_ONLY
    ecm_panel = gene_set & FIBROBLAST_CORE
    if collagen_present and len(ecm_panel) < 2:
        msg = (
            "Only collagen genes without DCN/LUM/PDGFRA — "
            "do not overclaim fibroblast subtype."
        )
        caveats.append(msg)
        comments.append(ReviewerComment(category="over_annotation", comment=msg, severity="warning"))
        adjustment -= 0.1

    activation = gene_set & ACTIVATED_MARKERS
    contractile = gene_set & MYOFIBROBLAST_MARKERS

    at2_markers = gene_set & AT2_MARKERS
    if "SFTPA" in gene_set:
        at2_markers = at2_markers | {"SFTPA"}
    if at2_markers and contractile:
        msg = (
            f"AT2 surfactant markers ({', '.join(sorted(at2_markers))}) co-present with "
            f"contractile markers ({', '.join(sorted(contractile))}) — consider epithelial–"
            "mesenchymal transition, doublet, or mixed cluster."
        )
        caveats.append(msg)
        alternatives.append("Myofibroblast / mesenchymal transition (secondary signal)")
        comments.append(ReviewerComment(category="mixed_identity", comment=msg, severity="warning"))
        adjustment -= 0.05

    if activation and not contractile:
        msg = (
            "POSTN/CTHRC1 present without ACTA2/TAGLN/MYH11 — "
            "prefer 'activated fibroblast' over 'myofibroblast'."
        )
        caveats.append(msg)
        alternatives.append("Activated fibroblast / disease-associated fibroblast")
        comments.append(ReviewerComment(category="subtype", comment=msg, severity="info"))

    if not contractile:
        myo_candidates = [c for c in marker_result.candidates if c.cell_type == "Myofibroblast"]
        if myo_candidates and myo_candidates[0].confidence_score > 0.3:
            msg = "Strong myofibroblast annotation is not justified without contractile markers."
            caveats.append(msg)
            adjustment -= 0.2

    for candidate in marker_result.candidates[:3]:
        missing_markers.extend(candidate.missing_expected_genes[:3])

    missing_markers = list(dict.fromkeys(missing_markers))[:10]

    tissue_mismatches = sum(
        1 for e in evidence_result.evidence_items if e.tissue_match is False
    )
    total_evidence = len(evidence_result.evidence_items)
    if total_evidence > 0 and tissue_mismatches > total_evidence / 2:
        msg = "Evidence is mostly non-tissue-matched — confidence reduced."
        caveats.append(msg)
        adjustment -= 0.15

    title_only = sum(
        1 for e in evidence_result.evidence_items
        if e.evidence_type in ("Title only", "Metadata only", "No direct evidence")
    )
    if total_evidence > 0 and title_only > total_evidence / 2:
        msg = "Most evidence is from title/metadata only — limited abstract support."
        caveats.append(msg)
        adjustment -= 0.1

    if len(user_input.genes) < 3:
        caveats.append("Fewer than 3 marker genes — annotation reliability is limited.")
        adjustment -= 0.1

    adjustment = max(-0.3, min(0.3, adjustment))

    return ReviewerResult(
        key_caveats=caveats,
        missing_markers=missing_markers,
        alternative_annotations=alternatives,
        contradictory_evidence=contradictory,
        reviewer_summary="",
        confidence_adjustment=adjustment,
        comments=comments,
    )


def run_reviewer_agent(
    user_input: UserInput,
    marker_result: MarkerAgentResult,
    evidence_result: EvidenceAgentResult,
    llm: Optional[LLMService] = None,
) -> ReviewerResult:
    """Run the Reviewer Agent with deterministic checks plus optional LLM narrative."""
    result = run_deterministic_reviewer_checks(user_input, marker_result, evidence_result)

    if llm:
        top_marker = marker_result.candidates[0] if marker_result.candidates else None
        evidence_summary = "\n".join(
            f"- {e.gene}: {e.predicted_cell_type} ({e.confidence_label}) PMID {e.pmid}"
            for e in evidence_result.evidence_items[:10]
        )
        prompt = (
            f"Genes: {', '.join(user_input.genes)}\n"
            f"Tissue: {user_input.tissue}, Disease: {user_input.disease}\n"
            f"Top marker candidate: {top_marker.cell_type if top_marker else 'none'}\n"
            f"Existing caveats: {'; '.join(result.key_caveats)}\n"
            f"Evidence:\n{evidence_summary or 'No literature evidence.'}\n\n"
            "As a strict single-cell reviewer, provide:\n"
            "- reviewer_summary (2-3 sentences)\n"
            "- alternative_annotations (plausible weaker alternatives)\n"
            "- contradictory_evidence (any conflicts)"
        )
        try:
            llm_out = llm.complete_json(
                system="Act as a strict single-cell RNA-seq cluster reviewer.",
                user=prompt,
                schema=_ReviewerLLMOutput,
            )
            result.reviewer_summary = llm_out.reviewer_summary
            result.alternative_annotations = list(
                dict.fromkeys(result.alternative_annotations + llm_out.alternative_annotations)
            )
            result.contradictory_evidence = llm_out.contradictory_evidence
        except Exception as exc:
            logger.warning("Reviewer LLM narrative failed: %s", exc, exc_info=True)
            result.reviewer_summary = (
                "Reviewer analysis based on deterministic marker and evidence checks."
            )

    if not result.reviewer_summary:
        result.reviewer_summary = (
            f"Reviewed {len(user_input.genes)} markers against "
            f"{len(marker_result.candidates)} candidates and "
            f"{len(evidence_result.evidence_items)} evidence items."
        )

    return result
