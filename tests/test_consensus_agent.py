"""Tests for consensus logic — no network or LLM calls."""
import pytest
from tests.fixtures.sample_state import (
    make_fibrosis_fibroblast_state,
    make_at2_state,
    make_weak_evidence_state,
)
from maple.models import AnalysisInput, CandidateLabel, CandidateResult, DevilsAdvocateResult, ExtractionResult
from maple.runtime.consensus_agent import run_consensus_agent


def test_fibroblast_consensus_is_medium_or_high():
    state = make_fibrosis_fibroblast_state()
    result = run_consensus_agent(
        state.input, state.extraction, state.candidates, state.devils_advocate, llm=None
    )
    assert result.confidence in ("High", "Medium")
    assert "fibroblast" in result.consensus_label.lower()


def test_weak_evidence_is_insufficient():
    state = make_weak_evidence_state()
    result = run_consensus_agent(
        state.input, state.extraction, state.candidates, state.devils_advocate, llm=None
    )
    assert result.confidence in ("Insufficient", "Low")


def test_consensus_label_comes_from_evidence():
    """The consensus label must come from evidence rows, not a hardcoded rule."""
    state = make_at2_state()
    result = run_consensus_agent(
        state.input, state.extraction, state.candidates, state.devils_advocate, llm=None
    )
    assert result.confidence in ("High", "Medium")
    # Must reference an AT2-related label from the evidence
    assert any(
        term in result.consensus_label.lower()
        for term in ["at2", "alveolar", "type 2", "epithelial"]
    )


def test_no_marker_rules_in_consensus():
    """Verify consensus_agent does not import marker_agent or load marker_rules.json."""
    import maple.runtime.consensus_agent as mod
    import inspect
    import re
    source = inspect.getsource(mod)
    # No actual import statement for marker_agent (docstring warnings mentioning it are fine)
    assert not re.search(r"^\s*(import|from)\s+.*marker_agent", source, re.MULTILINE)
    # No file-open or json-load of marker_rules.json
    assert not re.search(r"open\s*\(.*marker_rules", source)
    assert not re.search(r"json\.load.*marker_rules", source)


def test_full_panel_specific_candidate_not_downgraded_to_multiple():
    inp = AnalysisInput(markers=["TP63", "CDH2", "CDKN1A", "CDKN2A", "KRT17", "VIM"])
    candidates = CandidateResult(
        candidate_labels=[
            CandidateLabel(
                candidate_label="Aberrant basaloid",
                normalized_label="aberrant basaloid",
                supporting_genes=["CDH2", "CDKN1A", "CDKN2A", "KRT17", "TP63", "VIM"],
                supporting_pmids=["33859634", "40311757"],
                supporting_paper_count=2,
                specificity="specific",
                candidate_score=1.0,
            ),
            CandidateLabel(
                candidate_label="Club epithelial",
                normalized_label="club epithelial",
                supporting_genes=["CDH2", "CDKN1A", "CDKN2A", "KRT17", "TP63"],
                supporting_pmids=["42340362", "41388287"],
                supporting_paper_count=2,
                specificity="specific",
                candidate_score=0.9998,
            ),
        ]
    )
    da = DevilsAdvocateResult(recommended_confidence_adjustment="keep")

    result = run_consensus_agent(inp, ExtractionResult(), candidates, da, llm=None)

    assert result.consensus_label == "Aberrant basaloid"
    assert result.consensus_label != "Multiple cell types in literature"


def test_full_panel_at0_top_candidate_survives_capped_score_tie():
    inp = AnalysisInput(markers=["SCGB3A2", "SFTPB", "SFTPC"])
    candidates = CandidateResult(
        candidate_labels=[
            CandidateLabel(
                candidate_label="Alveolar type 0 epithelial",
                normalized_label="alveolar type 0 epithelial",
                supporting_genes=["SCGB3A2", "SFTPB", "SFTPC"],
                supporting_pmids=[str(i) for i in range(12)],
                supporting_paper_count=12,
                specificity="specific",
                candidate_score=1.0,
            ),
            CandidateLabel(
                candidate_label="Alveolar type 2 epithelial",
                normalized_label="alveolar type 2 epithelial",
                supporting_genes=["SCGB3A2", "SFTPB", "SFTPC"],
                supporting_pmids=[str(i) for i in range(10)],
                supporting_paper_count=10,
                specificity="specific",
                candidate_score=1.0,
            ),
        ]
    )

    result = run_consensus_agent(
        inp, ExtractionResult(), candidates, DevilsAdvocateResult(), llm=None
    )

    assert result.consensus_label == "Alveolar type 0 epithelial"
