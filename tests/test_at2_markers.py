"""Tests for AT2 vs myofibroblast disambiguation."""

from agents.consensus_agent import run_consensus_agent
from agents.evidence_agent import run_evidence_agent
from agents.marker_agent import run_marker_agent
from agents.reviewer_agent import run_deterministic_reviewer_checks
from models.schemas import EvidenceAgentResult, PubMedPaper, UserInput


def test_at2_markers_prefer_alveolar_type2_over_myofibroblast():
    genes = ["SFTPC", "SFTPB", "SFTPA", "ETV5", "ACTA2"]
    user_input = UserInput(
        genes=genes,
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )

    marker_result = run_marker_agent(user_input, llm=None)
    top = marker_result.candidates[0]
    assert top.cell_type == "Alveolar type 2 epithelial"
    assert top.confidence_score >= 0.5

    myo = [c for c in marker_result.candidates if c.cell_type == "Myofibroblast"]
    if myo:
        assert myo[0].confidence_score < top.confidence_score


def test_consensus_keeps_at2_with_mixed_acta2_evidence():
    genes = ["SFTPC", "SFTPB", "SFTPA", "ETV5", "ACTA2"]
    user_input = UserInput(
        genes=genes,
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )
    marker_result = run_marker_agent(user_input, llm=None)

    papers_by_gene = {
        gene: [
            PubMedPaper(
                pmid=f"1000{i}",
                title=f"{gene} expression in human lung fibrosis",
                abstract=(
                    f"{gene} is expressed in alveolar type 2 epithelial cells in "
                    "idiopathic pulmonary fibrosis lung tissue."
                ),
                journal="Test Journal",
                year=2023,
                authors=["Author A"],
                pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/1000{i}/",
            )
        ]
        for i, gene in enumerate(genes)
    }
    evidence_result = run_evidence_agent(
        user_input, papers_by_gene, marker_result, llm=None
    )
    reviewer_result = run_deterministic_reviewer_checks(
        user_input, marker_result, evidence_result
    )
    report = run_consensus_agent(
        user_input,
        marker_result,
        evidence_result,
        reviewer_result,
        llm=None,
    )

    assert report.consensus.final_cell_type == "Alveolar type 2 epithelial"
    assert any("ACTA2" in c or "contractile" in c.lower() for c in reviewer_result.key_caveats)
    assert "PubMed-primary" in report.consensus.pubmed_evidence_summary


def test_evidence_infers_from_literature_not_marker_lookup():
    user_input = UserInput(
        genes=["SFTPC"],
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )
    marker_result = run_marker_agent(user_input, llm=None)
    papers = {
        "SFTPC": [
            PubMedPaper(
                pmid="99999",
                title="Myofibroblast activation in pulmonary fibrosis",
                abstract="SFTPC marks alveolar type 2 epithelial cells in fibrotic lung.",
                journal="Test Journal",
                year=2022,
                authors=["Author B"],
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/99999/",
            )
        ]
    }
    result = run_evidence_agent(user_input, papers, marker_result, llm=None)
    assert len(result.evidence_items) == 1
    assert result.evidence_items[0].predicted_cell_type == "Alveolar type 2 epithelial"
    assert "Inferred from PubMed" in result.evidence_items[0].reasoning
