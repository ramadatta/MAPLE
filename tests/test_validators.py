"""Tests for evidence validation helpers."""
from maple.extraction.validators import (
    validate_label_in_snippet,
    validate_genes_in_snippet,
)


def test_label_must_appear_in_snippet():
    assert validate_label_in_snippet(
        "Goblet cell",
        "The monocle2 was performed for analyzing evolution process of basal cells.",
    ) is False
    assert validate_label_in_snippet(
        "Club epithelial",
        "This study explored molecular alterations in airway epithelial cells.",
    ) is False
    assert validate_label_in_snippet(
        "Basal epithelial",
        "TP63 marks basal cells in the airway epithelium.",
    ) is True


def test_genes_must_all_appear_in_snippet():
    assert validate_genes_in_snippet(
        ["TP63", "CDKN1A"],
        "TP63 marks basal cells in the airway epithelium.",
    ) is False
    assert validate_genes_in_snippet(
        ["TP63", "KRT17"],
        "These cells express TP63 and KRT17 in basal epithelium.",
    ) is True
