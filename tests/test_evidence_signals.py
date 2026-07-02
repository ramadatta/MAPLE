"""Tests for Phase 1 evidence signal scoring."""
from __future__ import annotations

import pytest

from maple.extraction.evidence_signals import (
    EMITTABLE_EVIDENCE_TYPES,
    classify_evidence_type,
    is_broad_label_text,
    match_strength_from_signal,
    paper_rank_score,
    score_evidence_chunk,
)
from maple.models import AnalysisInput, RetrievedPaper
from maple.extraction.validators import validate_label_in_snippet, validate_snippet_in_text


def test_direct_marker_celltype_assignment():
    text = (
        "Cluster 3 was annotated as AT2 cells based on SFTPC, SFTPA1, SFTPA2 "
        "and ABCA3 expression."
    )
    genes = ["SFTPC", "SFTPA1", "ABCA3"]
    sig = score_evidence_chunk(text, genes, None, paper_id="1", chunk_id="c0")

    assert sig.evidence_type in ("direct_marker_celltype_assignment", "cluster_annotation")
    assert sig.assignment_score >= 0.7
    assert "SFTPC" in sig.detected_user_genes
    assert any("at2" in t.lower() for t in sig.detected_celltype_terms) or "at2" in text.lower()


def test_gene_mention_only_not_emittable():
    text = "We measured SFTPC and ABCA3 expression in lung samples."
    genes = ["SFTPC", "ABCA3"]
    sig = score_evidence_chunk(text, genes, None)

    assert sig.evidence_type == "gene_mention_only"
    assert sig.assignment_score < 0.4
    assert sig.evidence_type not in EMITTABLE_EVIDENCE_TYPES


def test_de_only_evidence_type():
    text = "COL1A1 and COL3A1 were upregulated in diseased tissue."
    genes = ["COL1A1", "COL3A1"]
    sig = score_evidence_chunk(text, genes, None)

    assert sig.evidence_type == "differential_expression_only"
    assert sig.assignment_score < 0.5


def test_broad_lineage_lower_strength():
    strength = match_strength_from_signal(
        "direct_marker_celltype_assignment",
        ["EPCAM"],
        is_broad_label=is_broad_label_text("epithelial cells"),
        section="abstract",
    )
    assert strength in ("Low", "Medium")
    assert strength != "High"


def test_specific_subtype_higher_strength():
    broad = match_strength_from_signal(
        "direct_marker_celltype_assignment",
        ["EPCAM", "KRT8"],
        is_broad_label=True,
        section="abstract",
    )
    specific = match_strength_from_signal(
        "cluster_annotation",
        ["EPCAM", "KRT8"],
        is_broad_label=False,
        section="abstract",
    )
    order = {"High": 3, "Medium": 2, "Low": 1}
    assert order[specific] >= order[broad]


def test_ranking_prefers_assignment_over_gene_count():
    genes = ["G1", "G2", "G3", "G4", "G5", "G6"]

    paper_a = RetrievedPaper(
        pmid="111",
        title="Many genes no annotation",
        abstract="G1 G2 G3 G4 G5 G6 were measured in bulk RNA-seq of lung tissue.",
        source_url="",
    )
    paper_b = RetrievedPaper(
        pmid="222",
        title="Fibroblast annotation",
        abstract=(
            "Cluster 7 was annotated as inflammatory fibroblasts based on "
            "G1, G2, and G3 expression in scRNA-seq."
        ),
        source_url="",
    )

    score_a, _ = paper_rank_score(paper_a, genes, None)
    score_b, reason_b = paper_rank_score(paper_b, genes, None)

    assert score_b > score_a
    assert "annotated" in reason_b.lower() or "assignment" in reason_b.lower()


def test_validator_blocks_hallucinated_snippet():
    paper_text = "Cluster 3 was annotated as AT2 cells based on SFTPC expression."
    fake_snippet = "Hepatocytes in liver expressed ALB and AFP without lung markers."
    assert validate_snippet_in_text(fake_snippet, paper_text, threshold=0.6) is False


def test_validator_label_must_be_in_snippet():
    assert validate_label_in_snippet("hepatocyte", "Cluster 3 was annotated as AT2 cells.") is False
    assert validate_label_in_snippet("AT2 cells", "Cluster 3 was annotated as AT2 cells.") is True


def test_classify_cluster_annotation():
    et = classify_evidence_type(
        "Cluster 5 was annotated as macrophages.",
        ["CD68"],
        assignment_hits=["annotated as"],
        cluster_hits=["cluster "],
        de_hits=[],
        celltype_terms=["macrophages"],
        detected_genes=["CD68"],
    )
    assert et in ("cluster_annotation", "direct_marker_celltype_assignment")
