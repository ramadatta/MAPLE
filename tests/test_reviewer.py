"""Tests for reviewer agent deterministic rules."""

from agents.marker_agent import run_marker_agent
from agents.reviewer_agent import run_deterministic_reviewer_checks
from models.schemas import EvidenceAgentResult, UserInput


def test_no_strong_myofibroblast_without_contractile_markers():
    """POSTN, CTHRC1, COL1A1 without ACTA2/TAGLN/MYH11 should not justify myofibroblast."""
    genes = ["POSTN", "CTHRC1", "COL1A1"]
    user_input = UserInput(
        genes=genes,
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )
    marker_result = run_marker_agent(user_input, llm=None)
    evidence_result = EvidenceAgentResult()

    reviewer = run_deterministic_reviewer_checks(user_input, marker_result, evidence_result)

    myo_candidates = [c for c in marker_result.candidates if c.cell_type == "Myofibroblast"]
    if myo_candidates:
        assert myo_candidates[0].confidence_score < 0.5

    assert any("activated fibroblast" in c.lower() for c in reviewer.key_caveats) or any(
        "activated fibroblast" in a.lower() for a in reviewer.alternative_annotations
    )

    activated = [c for c in marker_result.candidates if c.cell_type == "Activated fibroblast"]
    if activated and myo_candidates:
        assert activated[0].confidence_score >= myo_candidates[0].confidence_score
