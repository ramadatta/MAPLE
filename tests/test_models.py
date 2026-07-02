"""Tests that models validate correctly."""
import pytest
from pydantic import ValidationError
from maple.models import AnalysisInput, EvidenceRow, ConsensusResult


def test_analysis_input_requires_markers():
    # Empty markers should fail validation
    with pytest.raises(ValidationError):
        AnalysisInput(markers=[])

    # Valid markers list should succeed
    inp = AnalysisInput(markers=["COL1A1"])
    assert inp.markers == ["COL1A1"]


def test_evidence_row_genes_count():
    row = EvidenceRow(
        pmid="123",
        paper_title="Test",
        celltype_label="AT2 cell",
        matched_user_genes=["SFTPC", "SFTPA1"],
        number_of_user_genes_found=2,
        evidence_snippet="SFTPC and SFTPA1 mark AT2 cells.",
    )
    assert row.number_of_user_genes_found == len(row.matched_user_genes)


def test_analysis_input_optional_context():
    inp = AnalysisInput(markers=["SFTPC", "SFTPA1"], tissue="lung", disease="IPF", species="human")
    assert inp.tissue == "lung"
    assert inp.disease == "IPF"
    assert inp.species == "human"
    assert inp.technology is None


def test_consensus_result_defaults():
    cr = ConsensusResult()
    assert cr.consensus_label == "Insufficient evidence"
    assert cr.confidence == "Insufficient"
    assert cr.supporting_genes == []
    assert cr.main_supporting_pmids == []
