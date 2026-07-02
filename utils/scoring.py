"""Pure scoring functions for marker overlap and consensus."""

from __future__ import annotations

from models.schemas import ConfidenceLabel


def clamp_score(score: float) -> float:
    """Clamp a score to the range [0.0, 1.0]."""
    return max(0.0, min(1.0, score))


def marker_overlap_score(input_genes: list[str], rule_genes: list[str]) -> float:
    """
    Compute overlap ratio between input genes and a marker rule set.

    Returns fraction of rule markers matched (0.0 to 1.0).
    """
    if not rule_genes:
        return 0.0
    input_set = {g.upper() for g in input_genes}
    matched = sum(1 for g in rule_genes if g.upper() in input_set)
    return matched / len(rule_genes)


def score_to_label(score: float) -> ConfidenceLabel:
    """
    Map a numeric confidence score to a label.

    Literature-first thresholds:
    - High (0.75+): Strong PubMed support (5+ PMIDs or multiple strong papers)
    - Medium (0.50+): Moderate PubMed support (2-4 PMIDs) or strong marker agreement
    - Low (0.25+): Weak PubMed support (1 PMID) or marker-only with partial overlap
    - Insufficient (<0.25): No literature support; marker fallback alone is unreliable
    """
    score = clamp_score(score)
    if score >= 0.75:
        return "High"
    if score >= 0.50:
        return "Medium"
    if score >= 0.25:
        return "Low"
    return "Insufficient"


def compute_consensus_score(
    base_score: float,
    evidence_boost: float = 0.0,
    reviewer_adjustment: float = 0.0,
    penalties: float = 0.0,
) -> float:
    """
    Combine base marker score with evidence boosts, reviewer adjustment, and penalties.

    All components are additive; result is clamped to [0.0, 1.0].
    """
    raw = base_score + evidence_boost + reviewer_adjustment - penalties
    return clamp_score(raw)


def evidence_boost_from_items(
    tissue_matches: int,
    disease_matches: int,
    high_confidence_count: int,
    total_evidence: int,
) -> float:
    """Compute evidence boost from aggregated evidence statistics."""
    if total_evidence == 0:
        return 0.0
    boost = 0.0
    boost += min(0.15, tissue_matches * 0.05)
    boost += min(0.15, disease_matches * 0.05)
    boost += min(0.2, high_confidence_count * 0.05)
    return boost


def aggregate_evidence_scores(
    evidence_items: list,
) -> dict[str, float]:
    """Sum confidence scores per predicted cell type from literature evidence."""
    scores: dict[str, float] = {}
    for item in evidence_items:
        if item.confidence_label == "Insufficient":
            continue
        scores[item.predicted_cell_type] = scores.get(item.predicted_cell_type, 0.0) + item.confidence_score
    return scores


def compute_literature_primary_score(
    evidence_items: list,
    winning_cell_type: str,
    marker_score: float,
    markers_agree: bool,
) -> float:
    """
    Confidence when PubMed literature drives the final annotation.

    LITERATURE-FIRST: Weights primarily by PMID count and evidence quality.
    - 1 PMID → 0.4 confidence
    - 2-4 PMIDs → 0.55-0.65 confidence
    - 5-9 PMIDs → 0.70-0.85 confidence
    - 10+ PMIDs → 0.90-1.0 confidence
    """
    type_items = [
        e for e in evidence_items
        if e.predicted_cell_type == winning_cell_type and e.confidence_label != "Insufficient"
    ]

    # No literature support for this cell type → marker fallback (capped)
    if not type_items:
        return clamp_score(marker_score * 0.4)

    # PRIMARY: Count unique PMIDs supporting this cell type
    unique_pmids = len({e.pmid for e in type_items if e.pmid})

    # SECONDARY: Quality of evidence (average confidence in the papers)
    avg_item_score = sum(e.confidence_score for e in type_items) / len(type_items)

    # TERTIARY: Gene coverage (how many input genes mentioned)
    coverage = len({e.gene for e in type_items})
    all_genes_in_evidence = len({e.gene for e in evidence_items}) or 1
    gene_factor = min(1.0, coverage / all_genes_in_evidence)

    # Compute PMID-based score (primary signal)
    # 1 PMID=0.40, 2=0.50, 3=0.60, 4=0.65, 5=0.75, 6-9=0.85, 10+=0.95
    if unique_pmids >= 10:
        pmid_score = 0.95
    elif unique_pmids >= 6:
        pmid_score = 0.85
    elif unique_pmids >= 5:
        pmid_score = 0.75
    elif unique_pmids >= 4:
        pmid_score = 0.65
    elif unique_pmids >= 3:
        pmid_score = 0.60
    elif unique_pmids >= 2:
        pmid_score = 0.50
    else:  # 1 PMID
        pmid_score = 0.40

    # Blend: PMID count (60%) + evidence quality (30%) + gene coverage (10%)
    score = pmid_score * 0.60 + avg_item_score * 0.30 + gene_factor * 0.10

    # Slight marker agreement boost (don't let it dominate)
    if markers_agree:
        score += 0.05

    return clamp_score(score)
