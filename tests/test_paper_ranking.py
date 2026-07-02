"""Tests for paper ranking by input-marker coverage."""

from agents.paper_ranking import final_cell_type_from_ranked, rank_papers_by_marker_coverage
from models.schemas import PubMedPaper
from utils.cell_type_lexicon import infer_cell_type_from_literature

GENES = [
    "TP63", "CDH2", "CDKN1A", "CDKN2A", "VIM", "KRT17",
    "LAMB3", "LAMC2", "FN1", "COL1A1", "TNC", "HMGA2",
]


def _papers():
    return {
        "KRT17": [
            PubMedPaper(
                pmid="32832599",
                title="scRNA-seq reveals aberrant basaloid cells in IPF",
                abstract=(
                    "We identify aberrant basaloid cells co-expressing KRT17, TP63, CDH2, "
                    "VIM, CDKN1A, CDKN2A, FN1, COL1A1 and HMGA2 in fibrotic human lung."
                ),
                journal="Sci Adv",
                year=2020,
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/32832599/",
            )
        ],
        "TP63": [
            PubMedPaper(
                pmid="37111111",
                title="TP63 marks basal cells of human airway",
                abstract="TP63, KRT17, LAMB3 and LAMC2 define basal cells in airway epithelium.",
                journal="J",
                year=2023,
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/37111111/",
            )
        ],
        "COL1A1": [
            PubMedPaper(
                pmid="41222222",
                title="COL1A1 in lung fibroblasts",
                abstract="COL1A1, FN1 and TNC are produced by fibroblasts in IPF.",
                journal="J",
                year=2024,
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/41222222/",
            )
        ],
    }


def test_lexicon_detects_aberrant_basaloid():
    cell_type, phrase = infer_cell_type_from_literature(
        "These aberrant basaloid cells are KRT5-/KRT17+ in fibrotic lung."
    )
    assert cell_type == "Aberrant basaloid"
    assert phrase is not None


def test_ranking_orders_papers_by_marker_count():
    ranked = rank_papers_by_marker_coverage(GENES, _papers())
    assert [p.pmid for p in ranked] == ["32832599", "37111111", "41222222"]
    assert ranked[0].marker_count == 9
    assert ranked[0].cell_type == "Aberrant basaloid"


def test_top_paper_drives_final_cell_type():
    ranked = rank_papers_by_marker_coverage(GENES, _papers())
    cell_type, top = final_cell_type_from_ranked(ranked)
    assert cell_type == "Aberrant basaloid"
    assert top.pmid == "32832599"


def test_ranking_skips_papers_without_input_markers():
    papers = {
        "TP63": [
            PubMedPaper(
                pmid="55555",
                title="Unrelated study",
                abstract="This paper discusses cardiac myocytes only.",
                year=2022,
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/55555/",
            )
        ]
    }
    assert rank_papers_by_marker_coverage(GENES, papers) == []


def test_whole_word_marker_matching():
    # 'VIM' must not match inside 'VIMENTIN'; 'TNC' must not match 'PTNCX'.
    papers = {
        "VIM": [
            PubMedPaper(
                pmid="60001",
                title="Vimentin biology",
                abstract="VIMENTIN expression and PTNCX were studied; no symbols present.",
                year=2021,
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/60001/",
            )
        ]
    }
    assert rank_papers_by_marker_coverage(GENES, papers) == []
