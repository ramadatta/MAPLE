"""Tests for MAPLE HTML UI components."""

from utils.ui_components import render_report_html, welcome_hero


def test_welcome_hero_contains_branding():
    html = welcome_hero()
    assert ">MAPLE<" in html
    assert "Marker-based Annotation with PubMed Literature Evidence" in html
    assert "maple-composer-anchor" in html
    assert "evidence table" in html.lower()
    # Example use cases and author credit are present.
    assert "TP63, CDH2, CDKN1A, CDKN2A, KRT17, VIM" in html
    assert "COL1A1, COL3A1, POSTN, DCN" in html
    assert "Sai Rama Sridatta Prakki" in html


def test_render_report_html_escapes_user_content():
    from models.schemas import (
        ConsensusResult,
        EvidenceAgentResult,
        FinalReport,
        MarkerAgentResult,
        ReviewerResult,
        UserInput,
    )

    report = FinalReport(
        user_input=UserInput(genes=["COL1A1"]),
        marker_result=MarkerAgentResult(candidates=[], input_genes=["COL1A1"]),
        evidence_result=EvidenceAgentResult(),
        reviewer_result=ReviewerResult(key_caveats=['<script>alert("x")</script>']),
        consensus=ConsensusResult(
            final_cell_type="Fibroblast<script>",
            confidence_label="Medium",
            confidence_score=0.55,
            umap_label="Fib_1",
            biological_interpretation="Test interpretation.",
        ),
    )
    html = render_report_html(report)
    assert "<script>" not in html
    assert "Fibroblast&lt;script&gt;" in html
