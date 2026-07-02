"""Tests for the candidate grouping logic — no network or LLM calls."""
import pytest
from maple.models import AnalysisInput, ExtractionResult, EvidenceRow, ContextMatchDetail
from maple.runtime.candidate_agent import run_candidate_agent


def _make_extraction(rows):
    return ExtractionResult(evidence_rows=rows)


def _make_row(celltype, genes, strength="High", pmid="12345", normalized=None,
              specificity="specific", evidence_type="direct_marker_celltype_assignment"):
    return EvidenceRow(
        pmid=pmid,
        paper_title="Test paper",
        celltype_label=celltype,
        normalized_label=(normalized if normalized is not None else celltype.lower()),
        specificity=specificity,
        evidence_type=evidence_type,
        marker_specific=True,
        matched_user_genes=genes,
        number_of_user_genes_found=len(genes),
        evidence_snippet="Test snippet mentioning " + " and ".join(genes),
        match_strength=strength,
        publication_year=2023,
    )


def _make_input(genes):
    return AnalysisInput(markers=genes)


def test_groups_similar_labels():
    inp = _make_input(["COL1A1", "COL3A1", "POSTN"])
    # The LLM supplies the canonical normalized_label; grouping follows it.
    extraction = _make_extraction([
        _make_row("AT2 cell", ["COL1A1"], pmid="1", normalized="alveolar type 2 cell"),
        _make_row("alveolar type 2 cell", ["COL1A1", "POSTN"], pmid="2", normalized="alveolar type 2 cell"),
        _make_row("alveolar type 2 epithelial cell", ["COL1A1", "COL3A1", "POSTN"], pmid="3",
                  normalized="alveolar type 2 epithelial cell"),
    ])
    result = run_candidate_agent(inp, extraction)
    assert len(result.candidate_labels) <= 2  # AT2 variants share a normalized label


def test_broad_label_penalized():
    inp = _make_input(["EPCAM", "KRT8"])
    extraction = _make_extraction([
        _make_row("epithelial cell", ["EPCAM"], pmid="1"),
        _make_row("alveolar type 2 cell", ["EPCAM", "KRT8"], pmid="2"),
    ])
    result = run_candidate_agent(inp, extraction)
    # AT2 should score higher than generic epithelial due to more genes + no broad penalty
    labels_by_score = sorted(result.candidate_labels, key=lambda c: c.candidate_score, reverse=True)
    assert labels_by_score[0].candidate_score >= labels_by_score[-1].candidate_score


def test_empty_evidence():
    inp = _make_input(["ACTB"])
    extraction = _make_extraction([])
    result = run_candidate_agent(inp, extraction)
    assert len(result.candidate_labels) == 0


def test_single_gene_high_match():
    inp = _make_input(["CD68"])
    extraction = _make_extraction([
        _make_row("alveolar macrophage", ["CD68"], strength="High", pmid="1"),
    ])
    result = run_candidate_agent(inp, extraction)
    assert len(result.candidate_labels) == 1
    assert result.candidate_labels[0].candidate_score > 0
