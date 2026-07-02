"""Marker Biology Agent — marker-rule-based cell type inference."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from models.schemas import MarkerAgentResult, MarkerCandidate, UserInput
from services.llm_service import LLMService
from utils.gene_aliases import AT2_MARKERS, expand_gene_set
from utils.scoring import clamp_score, marker_overlap_score, score_to_label

_MARKER_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "marker_rules.json"

FIBROBLAST_CORE = {"COL1A1", "COL3A1", "DCN", "LUM", "PDGFRA", "COL6A1", "COL6A2"}
ACTIVATED_MARKERS = {"POSTN", "CTHRC1", "FN1", "THBS2"}
MYOFIBROBLAST_MARKERS = {"ACTA2", "TAGLN", "MYH11", "CNN1", "TPM2"}
IMMUNE_MARKERS = {"PTPRC"}
EPITHELIAL_MARKERS = {"EPCAM", "KRT8", "KRT18", "KRT19", "KRT5", "KRT14", "TP63"}
BASAL_SQUAMOUS_MARKERS = {"TP63", "KRT5", "KRT14", "KRT17", "LAMB3", "LAMC2", "CDH2"}
ENDOTHELIAL_MARKERS = {"PECAM1", "VWF", "KDR", "EMCN", "CLDN5"}


def _load_marker_rules() -> dict[str, list[str]]:
    with open(_MARKER_RULES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {cell_type: info["markers"] for cell_type, info in data.items()}


def _deterministic_candidates(genes: list[str], rules: dict[str, list[str]]) -> list[MarkerCandidate]:
    """Score each cell type by marker overlap with biological rule adjustments."""
    gene_set = expand_gene_set(genes)
    input_set = {g.upper() for g in genes}
    candidates: list[MarkerCandidate] = []

    for cell_type, rule_markers in rules.items():
        matched = [g for g in rule_markers if g.upper() in gene_set]
        # Also report user-facing gene names (e.g. SFTPA not only SFTPA1)
        matched_display = []
        for g in genes:
            gu = g.upper()
            if gu in {m.upper() for m in rule_markers}:
                matched_display.append(gu)
            elif gu == "SFTPA" and cell_type == "Alveolar type 2 epithelial":
                matched_display.append(gu)
        if not matched and not matched_display:
            continue
        if not matched_display:
            matched_display = [m.upper() for m in matched]
        matched_display = list(dict.fromkeys(matched_display))

        overlap = marker_overlap_score(list(gene_set), rule_markers)
        missing = [g for g in rule_markers if g.upper() not in gene_set]
        score = overlap
        reasoning_parts = [
            f"Matched {len(matched_display)}/{len(rule_markers)} canonical markers "
            f"({', '.join(matched_display)})."
        ]

        if cell_type == "Alveolar type 2 epithelial":
            at2_matched = input_set & AT2_MARKERS
            if "SFTPA" in input_set:
                at2_matched = at2_matched | {"SFTPA"}
            if len(at2_matched) >= 2:
                score = min(1.0, score + 0.35)
                reasoning_parts.append(
                    f"Strong AT2 surfactant signature: {', '.join(sorted(at2_matched))}."
                )

        if cell_type == "Fibroblast":
            core_matched = gene_set & FIBROBLAST_CORE
            if len(core_matched) >= 3:
                score = min(1.0, score + 0.2)
                reasoning_parts.append(
                    f"Strong fibroblast signature: {', '.join(sorted(core_matched))}."
                )
            elif len(core_matched) < 2:
                score = max(0.0, score - 0.15)
                reasoning_parts.append(
                    "Weak fibroblast signature; only collagen genes without full ECM panel."
                )

        if cell_type == "Myofibroblast":
            contractile_matched = input_set & MYOFIBROBLAST_MARKERS
            at2_matched = input_set & AT2_MARKERS
            if "SFTPA" in input_set:
                at2_matched = at2_matched | {"SFTPA"}
            # Note: AT2 + contractile co-expression may indicate EMT or true doublet
            # but is not definitive — reduce penalty to allow literature to resolve
            if len(at2_matched) >= 2 and len(contractile_matched) <= 1:
                score = max(0.0, score - 0.25)
                reasoning_parts.append(
                    "AT2 and contractile markers co-present (possible EMT, doublet, or ambient RNA); "
                    "literature evidence should disambiguate."
                )
            elif not contractile_matched:
                score = max(0.0, score - 0.2)
                reasoning_parts.append(
                    "Few contractile markers (ACTA2/TAGLN/MYH11/CNN1); weaker myofibroblast support."
                )
            else:
                score = min(1.0, score + 0.1)
                reasoning_parts.append(
                    f"Contractile markers present: {', '.join(sorted(contractile_matched))}."
                )

        if cell_type == "Smooth muscle" and not (gene_set & MYOFIBROBLAST_MARKERS):
            score = max(0.0, score - 0.3)

        if cell_type == "Activated fibroblast":
            activation_matched = gene_set & ACTIVATED_MARKERS
            basal_matched = gene_set & BASAL_SQUAMOUS_MARKERS
            if len(basal_matched) >= 3:
                score = max(0.0, score - 0.35)
                reasoning_parts.append(
                    f"Basal/squamous epithelial markers ({', '.join(sorted(basal_matched))}) "
                    "argue against a primary activated fibroblast call."
                )
            elif activation_matched:
                score = min(1.0, score + 0.15)
                reasoning_parts.append(
                    f"Activation markers present: {', '.join(sorted(activation_matched))}."
                )

        if cell_type in ("Basal epithelial", "Epithelial"):
            basal_matched = gene_set & BASAL_SQUAMOUS_MARKERS
            if len(basal_matched) >= 3:
                score = min(1.0, score + 0.25)
                reasoning_parts.append(
                    f"Strong basal/squamous epithelial signature: "
                    f"{', '.join(sorted(basal_matched))}."
                )

        ambiguous: list[str] = []
        if matched_display and len(matched_display) == 1 and overlap < 0.3:
            ambiguous = matched_display.copy()
            score = max(0.0, score - 0.1)

        candidates.append(
            MarkerCandidate(
                cell_type=cell_type,
                matched_genes=matched_display,
                missing_expected_genes=missing,
                confidence_label=score_to_label(score),
                confidence_score=clamp_score(score),
                reasoning=" ".join(reasoning_parts),
                ambiguous_markers=ambiguous,
            )
        )

    candidates.sort(key=lambda c: c.confidence_score, reverse=True)
    return candidates


def _apply_lineage_flags(genes: list[str], candidates: list[MarkerCandidate]) -> list[str]:
    """Return warnings based on lineage marker presence."""
    gene_set = {g.upper() for g in genes}
    warnings: list[str] = []

    if gene_set & IMMUNE_MARKERS:
        warnings.append("PTPRC detected — consider immune lineage.")
    if gene_set & EPITHELIAL_MARKERS:
        warnings.append("Epithelial markers detected — consider epithelial identity or contamination.")
    if gene_set & ENDOTHELIAL_MARKERS:
        warnings.append("Endothelial markers detected — consider endothelial identity.")

    stromal = gene_set & (FIBROBLAST_CORE | ACTIVATED_MARKERS)
    if stromal and (gene_set & EPITHELIAL_MARKERS):
        warnings.append("Stromal and epithelial markers co-present — possible doublet or mixed cluster.")

    return warnings


def run_marker_agent(
    user_input: UserInput,
    llm: Optional[LLMService] = None,
) -> MarkerAgentResult:
    """
    Run the Marker Biology Agent.

    Uses deterministic marker-rule scoring with optional LLM refinement for reasoning.
    """
    rules = _load_marker_rules()
    candidates = _deterministic_candidates(user_input.genes, rules)
    warnings = _apply_lineage_flags(user_input.genes, candidates)
    warnings.extend(user_input.parse_warnings)

    if llm and candidates:
        top = candidates[:5]
        prompt = (
            f"Input genes: {', '.join(user_input.genes)}\n"
            f"Tissue: {user_input.tissue}\n"
            f"Disease: {user_input.disease}\n"
            f"Species: {user_input.species}\n\n"
            f"Top marker-based candidates:\n"
            + "\n".join(
                f"- {c.cell_type}: score={c.confidence_score:.2f}, "
                f"matched={c.matched_genes}, missing={c.missing_expected_genes}"
                for c in top
            )
            + "\n\nRefine reasoning for each candidate. Flag ambiguous markers. "
            "Do not change scores — only improve reasoning text."
        )
        try:
            refinement = llm.complete_text(
                system="Refine marker-based cell type reasoning. Be concise and scientific.",
                user=prompt,
            )
            if refinement and candidates:
                candidates[0] = candidates[0].model_copy(
                    update={"reasoning": f"{candidates[0].reasoning} {refinement[:500]}"}
                )
        except Exception as exc:
            logger.warning("Marker agent LLM refinement failed: %s", exc, exc_info=True)

    if not candidates:
        warnings.append("No marker-rule matches found for the provided genes.")

    return MarkerAgentResult(
        candidates=candidates,
        warnings=warnings,
        input_genes=user_input.genes,
    )
