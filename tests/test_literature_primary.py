"""Tests for literature-first evidence and consensus."""

from agents.consensus_agent import run_consensus_agent
from agents.evidence_agent import _heuristic_evidence, run_evidence_agent
from agents.marker_agent import run_marker_agent
from agents.reviewer_agent import run_deterministic_reviewer_checks
from models.schemas import EvidenceAgentResult, PubMedPaper, UserInput
from utils.cell_type_lexicon import infer_cell_type_from_literature


def test_lexicon_maps_at2_phrase():
    cell_type, phrase = infer_cell_type_from_literature(
        "SFTPC marks alveolar type 2 epithelial cells in fibrotic lung."
    )
    assert cell_type == "Alveolar type 2 epithelial"
    assert phrase is not None


def test_lexicon_maps_myofibroblast_phrase():
    cell_type, _ = infer_cell_type_from_literature(
        "ACTA2-positive myofibroblasts dominate fibrotic lung."
    )
    assert cell_type == "Myofibroblast"


def test_lexicon_maps_ectopic_endothelial_state_phrase():
    cell_type, phrase = infer_cell_type_from_literature(
        "VWA1+/PLVAP+ ectopic ECs showed high COL4A1 and COL4A2 expression."
    )
    assert cell_type == "Ectopic endothelial cell"
    assert phrase == "ectopic ecs"


def test_evidence_infers_cell_type_from_paper_text():
    user_input = UserInput(
        genes=["SFTPC"],
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )
    marker_result = run_marker_agent(user_input, llm=None)
    paper = PubMedPaper(
        pmid="99999",
        title="Myofibroblast activation in pulmonary fibrosis",
        abstract="SFTPC marks alveolar type 2 epithelial cells in fibrotic lung.",
        journal="Test Journal",
        year=2022,
        authors=["Author B"],
        pubmed_url="https://pubmed.ncbi.nlm.nih.gov/99999/",
    )
    item = _heuristic_evidence("SFTPC", paper, marker_result, user_input)
    assert item is not None
    assert item.predicted_cell_type == "Alveolar type 2 epithelial"
    assert "Inferred from PubMed" in item.reasoning


def test_implausible_literature_does_not_override_markers():
    """A literature cell type with no marker-panel support must not override the
    marker call; it is demoted to an alternative annotation instead."""
    user_input = UserInput(
        genes=["SFTPC"],
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )
    marker_result = run_marker_agent(user_input, llm=None)
    assert marker_result.candidates[0].cell_type == "Alveolar type 2 epithelial"

    papers = {
        "SFTPC": [
            PubMedPaper(
                pmid="88888",
                title="Single-cell IPF lung atlas",
                abstract=(
                    "SFTPC is upregulated in myofibroblasts during idiopathic pulmonary "
                    "fibrosis in human lung single-cell RNA-seq."
                ),
                journal="Test Journal",
                year=2024,
                authors=["Author C"],
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/88888/",
            )
        ]
    }
    evidence_result = run_evidence_agent(user_input, papers, marker_result, llm=None)
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

    # SFTPC is an AT2 marker; "myofibroblast" is an ambient literature mention.
    assert evidence_result.evidence_items[0].predicted_cell_type == "Myofibroblast"
    assert report.consensus.final_cell_type == "Alveolar type 2 epithelial"
    assert "Myofibroblast" in report.consensus.alternative_annotations


def test_plausible_literature_can_still_override_markers():
    """When the literature winner IS supported by the input markers, the
    literature-first override is preserved (gate does not block plausible calls)."""
    from models.schemas import GeneEvidence

    user_input = UserInput(
        genes=["COL1A1", "COL3A1", "DCN", "LUM", "PDGFRA", "POSTN"],
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )
    marker_result = run_marker_agent(user_input, llm=None)
    # POSTN makes "Activated fibroblast" a marker-supported candidate.
    supported = {c.cell_type for c in marker_result.candidates if c.matched_genes}
    assert "Activated fibroblast" in supported

    # Literature unambiguously points to a marker-supported type.
    evidence_result = EvidenceAgentResult(
        evidence_items=[
            GeneEvidence(
                gene=gene,
                predicted_cell_type="Activated fibroblast",
                evidence_sentence=f"{gene} marks activated fibroblasts in IPF.",
                evidence_type="Abstract",
                pmid=f"3000{i}",
                paper_title=f"{gene} study",
                confidence_label="Medium",
                confidence_score=0.6,
                reasoning="Inferred from PubMed gene context (matched 'activated fibroblast').",
                literature_inferred=True,
            )
            for i, gene in enumerate(["POSTN", "COL1A1", "COL3A1"])
        ]
    )
    reviewer_result = run_deterministic_reviewer_checks(
        user_input, marker_result, evidence_result
    )
    report = run_consensus_agent(
        user_input, marker_result, evidence_result, reviewer_result, llm=None
    )

    assert report.consensus.final_cell_type == "Activated fibroblast"


def test_basaloid_genes_not_called_macrophage():
    """Regression: an aberrant-basaloid panel must not be hijacked to Macrophage by
    broadly-expressed genes whose IPF papers merely mention macrophages."""
    genes = [
        "TP63", "CDH2", "CDKN1A", "CDKN2A", "VIM", "KRT17",
        "LAMB3", "LAMC2", "FN1", "COL1A1", "TNC", "HMGA2",
    ]
    user_input = UserInput(
        genes=genes,
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )
    marker_result = run_marker_agent(user_input, llm=None)
    assert marker_result.candidates[0].cell_type == "Basal epithelial"

    basal_abstract = "{g} marks basal cells in human airway epithelium."
    macro_abstract = "{g} correlates with macrophage infiltration in idiopathic pulmonary fibrosis."
    abstracts = {
        "TP63": basal_abstract,
        "KRT17": basal_abstract,
        "FN1": macro_abstract,
        "COL1A1": macro_abstract,
        "TNC": macro_abstract,
    }
    papers = {
        gene: [
            PubMedPaper(
                pmid=f"4000{i}",
                title=f"{gene} in IPF lung",
                abstract=tmpl.format(g=gene),
                journal="Test Journal",
                year=2024,
                authors=["Author F"],
                pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/4000{i}/",
            )
        ]
        for i, (gene, tmpl) in enumerate(abstracts.items())
    }
    evidence_result = run_evidence_agent(user_input, papers, marker_result, llm=None)
    reviewer_result = run_deterministic_reviewer_checks(
        user_input, marker_result, evidence_result
    )
    report = run_consensus_agent(
        user_input, marker_result, evidence_result, reviewer_result, llm=None
    )

    assert report.consensus.final_cell_type != "Macrophage"
    assert report.consensus.final_cell_type == "Basal epithelial"


def test_marker_rules_used_when_papers_lack_cell_type_phrases():
    user_input = UserInput(
        genes=["SFTPC", "SFTPB"],
        tissue="Human lung",
        disease="Idiopathic Pulmonary Fibrosis",
        species="Human",
    )
    marker_result = run_marker_agent(user_input, llm=None)
    papers = {
        gene: [
            PubMedPaper(
                pmid=f"2000{i}",
                title=f"{gene} in human lung fibrosis",
                abstract=f"{gene} was measured in idiopathic pulmonary fibrosis lung tissue.",
                journal="Test Journal",
                year=2023,
                authors=["Author D"],
                pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/2000{i}/",
            )
        ]
        for i, gene in enumerate(["SFTPC", "SFTPB"])
    }
    evidence_result = run_evidence_agent(user_input, papers, marker_result, llm=None)
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
    assert "marker fallback" in report.consensus.pubmed_evidence_summary.lower()
