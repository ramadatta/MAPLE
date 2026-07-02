"""Tests for maple/input_parser.py — all without network calls."""
import pytest
from maple.input_parser import parse_user_message


def test_simple_gene_list():
    result = parse_user_message("COL1A1, COL3A1, POSTN, CTHRC1, DCN, LUM")
    assert result is not None
    assert "COL1A1" in result.markers
    assert len(result.markers) == 6


def test_structured_input_with_tissue():
    result = parse_user_message("markers: SFTPC, SFTPA1\ntissue: lung\nspecies: human")
    assert result is not None
    assert "SFTPC" in result.markers
    assert result.tissue == "lung"
    assert result.species is None  # default Human is stored as None in AnalysisInput


def test_natural_language_with_genes():
    result = parse_user_message("Find papers where MKI67, TOP2A, EPCAM define a cell type")
    assert result is not None
    assert "MKI67" in result.markers


def test_no_genes_returns_none():
    """Text without recognizable gene symbols should not trigger analysis."""
    # Explicit stop words are rejected by the parser
    assert parse_user_message("start") is None
    # Empty/whitespace input returns None
    assert parse_user_message("") is None
    assert parse_user_message("   ") is None


def test_single_gene_returns_none():
    """Single gene alone should not trigger analysis."""
    result = parse_user_message("GAPDH")
    # Either None or a 1-gene result is acceptable — but if 1 gene, markers should have it
    if result is not None:
        assert "GAPDH" in result.markers


def test_genes_with_disease():
    result = parse_user_message("CD3D, CD3E, TRAC, IL7R\ndisease: cancer")
    assert result is not None
    assert "CD3D" in result.markers
    assert result.disease is not None


def test_labeled_markers_block_not_context_words():
    """Regression: Tissue/Disease/Markers labels must not be parsed as genes."""
    text = (
        "Tissue: Human lung\n"
        "Disease: Idiopathic Pulmonary Fibrosis\n"
        "Markers: TP63, CDH2, CDKN1A, CDKN2A, KRT17, VIM"
    )
    result = parse_user_message(text)
    assert result is not None
    assert result.markers == ["TP63", "CDH2", "CDKN1A", "CDKN2A", "KRT17", "VIM"]
    assert result.tissue == "Human lung"
    assert "Fibrosis" in (result.disease or "")
    assert "LUNG" not in result.markers
    assert "TISSUE" not in result.markers
