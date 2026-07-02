"""Tests for bulk annotation input parsing."""

from utils.bulk_input_parser import parse_annotation_input


USER_EXAMPLE = """Marker genes:
TP63, CDH2, CDKN1A, CDKN2A, VIM, KRT17, LAMB3, LAMC2, VIM, CDH2, FN1, COL1A1, TNC, HMGA2
Tissue: Human lung
Disease: Idiopathic Pulmonary Fibrosis
Species: Human"""


def test_parse_full_labeled_block():
    result = parse_annotation_input(USER_EXAMPLE)
    assert result is not None
    assert "TP63" in result.genes
    assert "COL1A1" in result.genes
    assert result.genes.count("VIM") == 1
    assert result.genes.count("CDH2") == 1
    assert result.tissue == "Human lung"
    assert result.disease == "Idiopathic Pulmonary Fibrosis"
    assert result.species == "Human"


def test_parse_inline_genes():
    raw = (
        "Marker genes: POSTN, COL1A1, DCN\n"
        "Tissue: Human lung\n"
        "Disease: IPF\n"
        "Species: Human"
    )
    result = parse_annotation_input(raw)
    assert result is not None
    assert result.genes == ["POSTN", "COL1A1", "DCN"]


def test_parse_gene_list_only():
    raw = "POSTN, COL1A1, DCN, LUM, COL3A1"
    result = parse_annotation_input(raw)
    assert result is not None
    assert len(result.genes) == 5
    # Plain gene lists have no tissue context (default is empty string)
    assert result.tissue == ""


def test_start_command_not_parsed_as_input():
    assert parse_annotation_input("start") is None
    assert parse_annotation_input("annotate") is None
