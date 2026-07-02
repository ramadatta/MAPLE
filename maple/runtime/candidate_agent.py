"""Candidate Agent — groups EvidenceRow objects into CandidateLabel objects.

Groups by normalized cell-type label, scores, and ranks candidates.
Cell type labels come from EvidenceRow.celltype_label (from paper text only).
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Optional

from maple.models import (
    AnalysisInput,
    ExtractionResult,
    EvidenceRow,
    CandidateLabel,
    CandidateResult,
    CandidateSpecificity,
)

logger = logging.getLogger(__name__)


def _normalize_label(label: str) -> str:
    """
    Generic, biology-agnostic normalization for grouping equivalent labels.

    The LLM supplies a `normalized_label` per evidence row; this function is only
    a fallback (and a light cleanup on top of the LLM value): lowercase, drop
    parenthetical asides, collapse whitespace, and singularise a trailing plural.
    No curated synonym maps or tissue-specific rules.
    """
    norm = re.sub(r"\s*\(.*?\)", "", label.strip().lower()).strip()
    norm = re.sub(r"\s+", " ", norm)
    if norm.endswith("s") and len(norm) > 4 and not norm.endswith("ss"):
        norm = norm[:-1]
    return norm


def _strength_score(strength: str) -> float:
    """Convert match strength to a numeric weight."""
    return {"High": 1.0, "Medium": 0.6, "Low": 0.3}.get(strength, 0.3)


def _group_specificity(rows: list[EvidenceRow]) -> CandidateSpecificity:
    """Most-specific specificity reported by the LLM across a group's rows."""
    order = {"specific": 2, "intermediate": 1, "broad": 0}
    best = max((order.get(r.specificity, 1) for r in rows), default=1)
    return {2: "specific", 1: "intermediate", 0: "broad"}[best]


def _specificity_sort_score(specificity: CandidateSpecificity) -> int:
    return {"specific": 2, "intermediate": 1, "broad": 0}.get(specificity, 1)


def _candidate_score(
    rows: list[EvidenceRow],
    total_papers: int,
    total_user_genes: int,
    specificity: CandidateSpecificity,
    analysis_input: AnalysisInput | None = None,
) -> float:
    """
    Compute candidate score for a group of evidence rows.

    Components (all derived from LLM-extracted evidence, no hardcoded labels):
    - Paper fraction: number of unique supporting PMIDs / total papers
    - Average match strength and evidence type
    - Gene coverage: fraction of user genes appearing in support
    - Recency + full-text bonuses
    - Specificity bonus and marker-specific fraction, both from the LLM
    """
    if not rows:
        return 0.0

    unique_pmids = len({r.pmid for r in rows})
    paper_fraction = unique_pmids / max(total_papers, 1)

    avg_strength = sum(_strength_score(r.match_strength) for r in rows) / len(rows)

    _type_weights = {
        "direct_marker_celltype_assignment": 1.0,
        "cluster_annotation": 0.85,
        "differential_expression_only": 0.35,
        "gene_mention_only": 0.1,
    }
    avg_type = sum(_type_weights.get(r.evidence_type, 0.3) for r in rows) / len(rows)

    all_supporting_genes: set[str] = set()
    for r in rows:
        all_supporting_genes.update(g.upper() for g in r.matched_user_genes)
    gene_fraction = len(all_supporting_genes) / max(total_user_genes, 1)

    recent_count = sum(1 for r in rows if (r.publication_year or 0) >= 2020)
    recency_bonus = 0.05 * (recent_count / len(rows))

    has_fulltext = any(
        r.evidence_section in ("results", "figure", "table", "supplement") for r in rows
    )
    fulltext_bonus = 0.1 if has_fulltext else 0.0

    score = (
        paper_fraction * 0.25
        + avg_strength * 0.25
        + avg_type * 0.18
        + gene_fraction * 0.25
        + recency_bonus
        + fulltext_bonus
    )

    # Reward candidates that explain most of the submitted marker panel.
    if gene_fraction >= 0.5:
        score += 0.12
    if gene_fraction >= 0.75:
        score += 0.10

    max_row_genes = max(len(r.matched_user_genes) for r in rows)
    score += 0.10 * (max_row_genes / max(total_user_genes, 1))

    if specificity == "specific":
        score += 0.08
    elif specificity == "intermediate":
        score += 0.02
    else:  # broad / lineage-level label
        score *= 0.7

    # Reward groups the model flagged as specific/defining marker evidence.
    marker_specific_fraction = sum(1 for r in rows if r.marker_specific) / len(rows)
    score += 0.08 * marker_specific_fraction

    if total_user_genes >= 4 and gene_fraction < 0.34 and unique_pmids <= 1:
        score *= 0.65

    return min(score, 1.0)


def run_candidate_agent(
    analysis_input: AnalysisInput,
    extraction_result: ExtractionResult,
) -> CandidateResult:
    """
    Group EvidenceRow objects into CandidateLabel objects.

    1. Normalize cell type labels.
    2. Group rows by normalized label.
    3. Score and rank candidates.
    """
    rows = extraction_result.evidence_rows
    audit_notes: list[str] = []

    if not rows:
        audit_notes.append("No evidence rows to group into candidates")
        return CandidateResult(audit_notes=audit_notes)

    strong_rows = [
        r for r in rows
        if r.evidence_type in ("direct_marker_celltype_assignment", "cluster_annotation")
    ]
    if strong_rows:
        dropped = len(rows) - len(strong_rows)
        rows = strong_rows
        if dropped:
            audit_notes.append(
                f"Excluded {dropped} weak DE-only/gene-mention row(s) from candidate scoring"
            )

    total_papers = max(1, len({r.pmid for r in rows}))
    total_user_genes = max(1, len(analysis_input.markers))

    # Group rows by normalized label
    groups: dict[str, list[EvidenceRow]] = defaultdict(list)
    norm_to_original: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        # Prefer the LLM-supplied canonical label; fall back to generic cleanup.
        norm = _normalize_label(row.normalized_label or row.celltype_label)
        groups[norm].append(row)
        if row.celltype_label not in norm_to_original[norm]:
            norm_to_original[norm].append(row.celltype_label)

    audit_notes.append(
        f"Grouped {len(rows)} evidence rows into {len(groups)} candidate labels"
    )

    # Build CandidateLabel for each group
    candidates: list[CandidateLabel] = []
    all_row_list = list(rows)  # for index lookup

    for norm_label, group_rows in groups.items():
        supporting_genes_set: set[str] = set()
        supporting_pmids: list[str] = []
        seen_pmids: set[str] = set()

        for r in group_rows:
            supporting_genes_set.update(g.upper() for g in r.matched_user_genes)
            if r.pmid not in seen_pmids:
                supporting_pmids.append(r.pmid)
                seen_pmids.add(r.pmid)

        # Find indices of best (highest match_strength) rows in the global list
        best_rows = sorted(group_rows, key=lambda r: _strength_score(r.match_strength), reverse=True)
        best_indices = [all_row_list.index(r) for r in best_rows[:5] if r in all_row_list]

        spec = _group_specificity(group_rows)
        score = _candidate_score(
            group_rows, total_papers, total_user_genes, spec, analysis_input
        )

        # Choose the most common original label as the display label
        original_labels = norm_to_original[norm_label]
        from collections import Counter
        label_counts = Counter(r.celltype_label for r in group_rows)
        display_label = label_counts.most_common(1)[0][0]

        candidates.append(
            CandidateLabel(
                candidate_label=display_label,
                normalized_label=norm_label,
                original_paper_labels=original_labels,
                supporting_genes=sorted(supporting_genes_set),
                supporting_pmids=supporting_pmids,
                supporting_paper_count=len(supporting_pmids),
                best_evidence_row_indices=best_indices,
                specificity=spec,
                candidate_score=round(score, 4),
                candidate_rationale=(
                    f"{len(group_rows)} evidence row(s) from {len(supporting_pmids)} paper(s); "
                    f"genes: {', '.join(sorted(supporting_genes_set)[:5])}"
                ),
            )
        )

    # Sort by score descending, then prefer candidates that explain more of the
    # submitted panel and use a more specific paper label.
    candidates.sort(
        key=lambda c: (
            c.candidate_score,
            len(c.supporting_genes),
            _specificity_sort_score(c.specificity),
            c.supporting_paper_count,
        ),
        reverse=True,
    )

    audit_notes.append(
        f"Top candidate: '{candidates[0].candidate_label}' "
        f"(score={candidates[0].candidate_score:.3f})"
        if candidates else "No candidates identified"
    )

    return CandidateResult(
        candidate_labels=candidates,
        audit_notes=audit_notes,
    )
