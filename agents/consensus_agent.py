"""Consensus Agent — synthesize final cell-type annotation report."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from agents.marker_agent import _load_marker_rules
from models.schemas import (
    ConsensusResult,
    EvidenceAgentResult,
    FinalReport,
    MarkerAgentResult,
    ReviewerResult,
    UserInput,
)
from services.llm_service import LLMService
from utils.gene_aliases import cell_type_for_gene
from utils.scoring import (
    aggregate_evidence_scores,
    clamp_score,
    compute_consensus_score,
    compute_literature_primary_score,
    evidence_boost_from_items,
    score_to_label,
)


class _ConsensusNarrative(BaseModel):
    """LLM narrative fields for consensus."""

    biological_interpretation: str = ""
    concise_summary: str = ""
    recommended_next_markers: list[str] = Field(default_factory=list)


def _compute_base_score(marker_result: MarkerAgentResult) -> tuple[str, float]:
    """Select top candidate cell type and base score from marker agent."""
    if not marker_result.candidates:
        return "Unknown", 0.2
    top = marker_result.candidates[0]
    return top.cell_type, top.confidence_score


def _literature_inferred_items(evidence_result: EvidenceAgentResult) -> list:
    """Evidence rows where cell type came from PubMed text, not marker fallback."""
    return [
        item
        for item in evidence_result.evidence_items
        if item.confidence_label != "Insufficient" and item.literature_inferred
    ]


STRONG_MARKER_THRESHOLD = 0.70


def _marker_supported_types(marker_result: MarkerAgentResult) -> set[str]:
    """Cell types the input panel actually backs (>=1 matched gene, score > 0)."""
    return {
        c.cell_type
        for c in marker_result.candidates
        if c.matched_genes and c.confidence_score > 0
    }


def _literature_type_is_plausible(
    cell_type: str,
    supporting_items: list,
    marker_supported: set[str],
    rules: dict,
) -> bool:
    """
    A literature-derived cell type may drive the call only if the marker panel
    supports it: it is itself a marker candidate, or at least one supporting gene
    is a canonical marker of that type. This blocks "ambient" mentions (e.g. a
    broadly-expressed gene's paper that merely discusses macrophages) from
    hijacking the annotation.

    When no marker rules exist for the tissue (marker_supported is empty), the
    gate is skipped and literature drives the call directly. This enables
    multi-tissue operation beyond the built-in lung rule set.
    """
    if not marker_supported:
        return True
    if cell_type in marker_supported:
        return True
    return any(cell_type_for_gene(item.gene, rules) == cell_type for item in supporting_items)


def _resolve_final_cell_type(
    marker_result: MarkerAgentResult,
    evidence_result: EvidenceAgentResult,
) -> tuple[str, str, bool, Optional[str]]:
    """
    Choose final cell type with PubMed literature as primary signal, gated by
    marker-panel plausibility.

    Returns (cell_type, annotation_source, markers_agree, demoted_literature_type).
    demoted_literature_type is a literature winner rejected for lacking marker
    support; it is surfaced downstream as an alternative annotation + caveat.
    """
    marker_top = marker_result.candidates[0] if marker_result.candidates else None
    marker_type = marker_top.cell_type if marker_top else "Unknown"

    literature_items = _literature_inferred_items(evidence_result)
    literature_scores = aggregate_evidence_scores(literature_items)

    if literature_scores:
        rules = _load_marker_rules()
        supported_types = _marker_supported_types(marker_result)
        raw_winner = max(literature_scores, key=literature_scores.get)

        # Keep only literature types the marker panel actually backs.
        plausible_scores = {
            cell_type: weight
            for cell_type, weight in literature_scores.items()
            if _literature_type_is_plausible(
                cell_type,
                [e for e in literature_items if e.predicted_cell_type == cell_type],
                supported_types,
                rules,
            )
        }
        # The raw top vote, if it was rejected, is surfaced as an alternative.
        demoted = raw_winner if raw_winner not in plausible_scores else None

        if plausible_scores:
            winning_type = max(plausible_scores, key=plausible_scores.get)
            if plausible_scores[winning_type] >= 0.35:
                markers_agree = winning_type == marker_type
                mixed = (
                    demoted is not None
                    or len(literature_items) < len(evidence_result.evidence_items)
                )
                source = "literature_mixed" if mixed else "literature"
                return winning_type, source, markers_agree, demoted

        # No plausible literature type cleared the bar → marker rules.
        return marker_type, "marker_rules", True, demoted

    if evidence_result.evidence_items:
        return marker_type, "marker_rules", True, None

    return marker_type, "marker_rules", True, None


def run_consensus_agent(
    user_input: UserInput,
    marker_result: MarkerAgentResult,
    evidence_result: EvidenceAgentResult,
    reviewer_result: ReviewerResult,
    llm: Optional[LLMService] = None,
    pubmed_warnings: Optional[list[str]] = None,
) -> FinalReport:
    """Synthesize marker, evidence, and reviewer outputs into a FinalReport."""
    _, base_score = _compute_base_score(marker_result)
    final_cell_type, annotation_source, markers_agree, demoted_lit_type = _resolve_final_cell_type(
        marker_result, evidence_result
    )
    literature_items = _literature_inferred_items(evidence_result)

    tissue_matches = sum(1 for e in evidence_result.evidence_items if e.tissue_match is True)
    disease_matches = sum(1 for e in evidence_result.evidence_items if e.disease_match is True)
    high_conf = sum(1 for e in evidence_result.evidence_items if e.confidence_label == "High")
    evidence_boost = evidence_boost_from_items(
        tissue_matches, disease_matches, high_conf, len(evidence_result.evidence_items)
    )

    if annotation_source in ("literature", "literature_mixed"):
        # PRIMARY: PubMed evidence drives the annotation
        final_score = compute_literature_primary_score(
            evidence_result.evidence_items,
            final_cell_type,
            base_score,
            markers_agree,
        )
        # Penalize mixed evidence (some papers lack explicit cell-type phrases)
        if annotation_source == "literature_mixed":
            final_score = clamp_score(final_score * 0.90)

        # Add tissue/disease match boosts
        final_score = clamp_score(
            final_score + evidence_boost + reviewer_result.confidence_adjustment
        )

        # Penalize when markers disagree with literature
        if not markers_agree and marker_result.candidates:
            final_score = clamp_score(final_score - 0.05)
    else:
        # FALLBACK: Marker rules only (no PubMed support for this cell type)
        # Literature-first principle: marker-only annotations have LOW ceiling

        # Hard rule: Zero PubMed evidence items → max confidence 0.25 ("Low")
        if len(evidence_result.evidence_items) == 0:
            final_score = clamp_score(base_score * 0.3)  # Harsh discount
        else:
            # Some PubMed papers retrieved but didn't mention this cell type
            # This is less confident than literature-driven, but not zero
            final_score = clamp_score(base_score * 0.5)

        # Apply additional penalties for sparse input
        if len(user_input.genes) < 3:
            final_score = clamp_score(final_score - 0.05)

        # Add reviewer adjustment (often a penalty if caveats exist)
        final_score = clamp_score(final_score + reviewer_result.confidence_adjustment)

        # Ensure marker-only fallback never exceeds "Low" confidence
        final_score = min(0.25, final_score)

    evidence_genes = list(
        dict.fromkeys(e.gene for e in evidence_result.evidence_items if e.confidence_label != "Insufficient")
    )
    supporting_pmids = list(dict.fromkeys(e.pmid for e in evidence_result.evidence_items if e.pmid))
    strongest_papers = [
        f"{e.paper_title} (PMID {e.pmid})"
        for e in sorted(
            evidence_result.evidence_items,
            key=lambda x: x.confidence_score,
            reverse=True,
        )[:5]
        if e.confidence_label in ("High", "Medium")
    ]
    weak_evidence = [
        f"{e.gene}: {e.evidence_sentence[:120]}"
        for e in evidence_result.evidence_items
        if e.confidence_label in ("Low", "Insufficient")
    ]

    umap_label = final_cell_type
    if len(umap_label) > 40:
        umap_label = umap_label[:37] + "..."

    marker_inference = ""
    if marker_result.candidates:
        top = marker_result.candidates[0]
        marker_inference = (
            f"Marker-based inference: {top.cell_type} "
            f"(score {top.confidence_score:.2f}, matched: {', '.join(top.matched_genes)})."
        )
        # Flag when literature contradicts markers
        if annotation_source in ("literature", "literature_mixed") and top.cell_type != final_cell_type:
            marker_inference += (
                f" [NOTE: Literature voted for {final_cell_type}, overriding marker inference]"
            )

    lit_count = len(literature_items)
    if evidence_result.evidence_items:
        if annotation_source == "literature":
            pubmed_summary = (
                f"**PubMed-primary (high confidence):** {lit_count} literature-inferred items from "
                f"{len(supporting_pmids)} papers explicitly support **{final_cell_type}**."
            )
        elif annotation_source == "literature_mixed":
            pubmed_summary = (
                f"**PubMed-weighted (moderate confidence):** {len(evidence_result.evidence_items)} evidence items "
                f"({lit_count} with explicit cell-type phrases) from {len(supporting_pmids)} papers. "
                f"Some genes retrieved but lacked direct cell-type mentions."
            )
        else:
            pubmed_summary = (
                f"⚠️ **MARKER FALLBACK (low confidence):** No explicit cell-type phrases found in PubMed text. "
                f"Annotation based on marker rules only. Retrieved {len(supporting_pmids)} papers for "
                f"{len(evidence_result.evidence_items)} gene mentions, but did not support **{final_cell_type}** by name."
            )
    else:
        pubmed_summary = (
            f"⚠️ **MARKER FALLBACK (insufficient evidence):** No PubMed papers retrieved for any input genes. "
            f"Annotation relies entirely on marker rules ({final_cell_type}), with NO literature support."
        )

    caveats = list(reviewer_result.key_caveats)
    alternative_annotations = list(reviewer_result.alternative_annotations)
    if demoted_lit_type:
        caveats.insert(
            0,
            f"Literature frequently mentions {demoted_lit_type} alongside these genes, but the "
            f"marker panel does not support it; kept marker-based call ({final_cell_type}).",
        )
        if demoted_lit_type not in alternative_annotations:
            alternative_annotations.append(demoted_lit_type)
    reviewer_summary_text = "; ".join(caveats[:5]) or "No major caveats."

    narrative = _ConsensusNarrative()
    if llm:
        prompt = (
            f"Final cell type: {final_cell_type}\n"
            f"Confidence: {final_score:.2f}\n"
            f"Genes: {', '.join(user_input.genes)}\n"
            f"Tissue: {user_input.tissue}, Disease: {user_input.disease}\n"
            f"Marker inference: {marker_inference}\n"
            f"PubMed: {pubmed_summary}\n"
            f"Caveats: {reviewer_summary_text}\n"
            f"Missing markers: {', '.join(reviewer_result.missing_markers[:5])}\n\n"
            "Provide biological_interpretation (one paragraph), concise_summary (2 sentences), "
            "and recommended_next_markers (genes to distinguish subtypes)."
        )
        try:
            narrative = llm.complete_json(
                system="Write concise scientific interpretation for a cell-type annotation report.",
                user=prompt,
                schema=_ConsensusNarrative,
            )
        except Exception as exc:
            logger.warning("Consensus LLM narrative failed: %s", exc, exc_info=True)

    if not narrative.biological_interpretation:
        if annotation_source == "literature":
            lead = (
                f"PubMed literature primarily supports {final_cell_type} in {user_input.tissue} "
                f"under {user_input.disease} conditions."
            )
        elif annotation_source == "literature_mixed":
            lead = (
                f"PubMed evidence (with some marker-rule fallbacks where papers lacked cell-type "
                f"phrases) supports {final_cell_type} in {user_input.tissue}."
            )
        else:
            lead = (
                f"Retrieved papers did not name a cell type explicitly; annotation falls back to "
                f"marker rules ({final_cell_type}) for {user_input.tissue}."
            )
        if not markers_agree and marker_result.candidates:
            lead += (
                f" Note: marker rules suggested {marker_result.candidates[0].cell_type}, "
                "which differs from the literature-led call."
            )
        narrative.biological_interpretation = f"{lead} {pubmed_summary} {reviewer_summary_text}"

    if not narrative.concise_summary:
        narrative.concise_summary = (
            f"Annotation: {final_cell_type} (confidence {score_to_label(final_score)}). "
            f"Based on {len(user_input.genes)} marker genes with "
            f"{len(evidence_result.evidence_items)} PubMed evidence items."
        )

    if not narrative.recommended_next_markers:
        narrative.recommended_next_markers = reviewer_result.missing_markers[:5]
        if not narrative.recommended_next_markers:
            narrative.recommended_next_markers = ["ACTA2", "TAGLN", "MYH11", "CNN1"]

    consensus = ConsensusResult(
        final_cell_type=final_cell_type,
        confidence_score=clamp_score(final_score),
        confidence_label=score_to_label(final_score),
        evidence_genes=evidence_genes,
        supporting_pmids=supporting_pmids,
        strongest_supporting_papers=strongest_papers,
        conflicting_or_weak_evidence=weak_evidence,
        missing_markers=reviewer_result.missing_markers,
        alternative_annotations=alternative_annotations,
        umap_label=umap_label,
        biological_interpretation=narrative.biological_interpretation,
        concise_summary=narrative.concise_summary,
        recommended_next_markers=narrative.recommended_next_markers,
        marker_based_inference=marker_inference,
        pubmed_evidence_summary=pubmed_summary,
        reviewer_caveats_summary=reviewer_summary_text,
    )

    return FinalReport(
        user_input=user_input,
        marker_result=marker_result,
        evidence_result=evidence_result,
        reviewer_result=reviewer_result,
        consensus=consensus,
        pubmed_warnings=pubmed_warnings or [],
    )
