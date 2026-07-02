"""Tests for gene parsing utilities."""

from utils.gene_parser import parse_genes


def test_parse_mixed_separators():
    raw = "COL1A1, col3a1\n POSTN  CTHRC1"
    genes, warnings = parse_genes(raw)
    assert genes == ["COL1A1", "COL3A1", "POSTN", "CTHRC1"]


def test_deduplicate_preserving_order():
    raw = "POSTN, postn, COL1A1, COL1A1"
    genes, _ = parse_genes(raw)
    assert genes == ["POSTN", "COL1A1"]


def test_empty_tokens_removed():
    raw = "COL1A1,, , COL3A1"
    genes, _ = parse_genes(raw)
    assert genes == ["COL1A1", "COL3A1"]


def test_few_genes_warning():
    raw = "POSTN, COL1A1"
    genes, warnings = parse_genes(raw)
    assert len(genes) == 2
    assert any("3" in w for w in warnings)


def test_empty_input():
    genes, warnings = parse_genes("")
    assert genes == []
    assert len(warnings) > 0
