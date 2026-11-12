"""Tests for gene aliases (incl. mouse spellings) and full-text passage selection."""
from maples.extraction.passages import SEPARATOR, chunk_text, select_passages, strip_references
from maples.genes import canonical_symbol, describe_for_prompt, gene_in_text, genes_in_text


def test_mouse_spelling_and_aliases_match():
    assert gene_in_text("KRT17", "Krt17+ cells")                       # mouse case
    assert gene_in_text("TP63", "p63-positive basal cells")
    assert gene_in_text("CDKN1A", "p21WAF1/CIP1 expression")
    assert gene_in_text("ACTA2", "α-SMA+ myofibroblasts")
    assert gene_in_text("HLA-DRA", "H2-Ea was induced")                # mouse ortholog
    assert not gene_in_text("TP63", "TP631 and p630")                  # whole tokens only
    assert not gene_in_text("KRT5", "KRT57")


def test_genes_in_text_keeps_user_symbols_and_order():
    assert genes_in_text(["VIM", "TP63", "SOX4"], "p63 and vimentin") == ["VIM", "TP63"]


def test_canonical_symbol_maps_model_names_back():
    genes = ["TP63", "KRT17"]
    assert canonical_symbol("p63", genes) == "TP63"
    assert canonical_symbol("Krt17", genes) == "KRT17"
    assert canonical_symbol("SOX9", genes) is None


def test_describe_for_prompt():
    text = describe_for_prompt(["TP63", "SOX4"])
    assert text.startswith("TP63 (may appear as p63") and text.endswith(", SOX4")


def test_strip_references_cuts_the_tail_only():
    body = "Intro text. " * 50 + "Results mention KRT17. " * 20
    text = body + "References Smith J et al. KRT5 paper."
    assert strip_references(text) == body
    early = "References to prior work. " + body          # early mention is kept
    assert strip_references(early) == early


def test_chunks_are_exact_substrings():
    text = "First sentence here. " * 80
    for start, chunk in chunk_text(text, size=200):
        assert text[start:start + len(chunk)] == chunk


def test_select_passages_prefers_gene_chunks_within_budget():
    filler = "Background without genes of interest. " * 60
    hit1 = "KRT5- KRT17+ aberrant basaloid cells were identified as a distinct cluster. "
    hit2 = "Vimentin and p63 marked transitional cells in Figure 3. "
    text = filler + hit1 + filler + hit2 + filler
    selected, stats = select_passages(text, ["KRT17", "KRT5", "VIM", "TP63"], max_chars=2500)
    assert "aberrant basaloid" in selected and "Vimentin and p63" in selected
    assert len(selected) <= 2500
    assert SEPARATOR in selected            # non-adjacent chunks are marked
    assert stats.chunks_with_genes >= 2 and stats.original_chars == len(text)


def test_select_passages_falls_back_to_start_without_genes():
    text = "Nothing relevant. " * 100
    selected, stats = select_passages(text, ["KRT17"], max_chars=300)
    assert selected == text[:300] and stats.chunks_with_genes == 0
