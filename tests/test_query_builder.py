"""Tests for PubMed query builder."""

from agents.marker_agent import run_marker_agent
from agents.query_builder import DISCOVERY_BUCKET, build_queries, _gene_combos, _tissue_keyword
from models.schemas import UserInput
from utils.gene_aliases import pubmed_gene_query


def test_tissue_keyword_extraction():
    assert _tissue_keyword("Human lung") == "lung"
    assert _tissue_keyword("Mouse liver") == "liver"


def test_queries_use_gene_field_and_lung():
    ui = UserInput(genes=["COL1A1", "TP63", "KRT17"], tissue="Human lung", disease="IPF")
    mr = run_marker_agent(ui)
    queries = build_queries(ui, mr)
    assert any("[Gene]" in q.query for q in queries)
    assert any("lung" in q.query.lower() for q in queries)
    assert not any("Human lung AND Idiopathic" in q.query for q in queries)


def test_gene_combos_sliding_window():
    combos = _gene_combos(["A", "B", "C", "D"], size=3, max_combos=3)
    assert combos == [("A", "B", "C"), ("B", "C", "D")]
    assert _gene_combos(["A", "B"], size=3) == [("A", "B")]


def test_discovery_queries_present():
    genes = ["TP63", "CDH2", "CDKN1A", "VIM", "KRT17", "FN1", "COL1A1"]
    ui = UserInput(genes=genes, tissue="Human lung", disease="Idiopathic Pulmonary Fibrosis")
    queries = build_queries(ui, run_marker_agent(ui))
    disc = [q for q in queries if q.query_type == "discovery"]

    # Atlas, population, and combination discovery queries should all appear.
    assert any("single-cell" in q.query.lower() for q in disc)
    assert any(" AND " in q.query and "[Gene]" in q.query for q in disc)
    # Discovery buckets must not collide with real gene names.
    assert all(q.gene.startswith(DISCOVERY_BUCKET) for q in disc)
    # A population query should name a marker candidate phrase.
    assert any('"' in q.query for q in disc)


def test_per_gene_has_progressive_tiers():
    ui = UserInput(genes=["FN1", "COL1A1"], tissue="Human lung")
    mr = run_marker_agent(ui)
    queries = build_queries(ui, mr)
    fn1 = [q for q in queries if q.gene == "FN1"]
    assert len(fn1) >= 3
    priorities = sorted(q.priority for q in fn1)
    assert priorities[0] <= priorities[-1]


def test_pubmed_gene_query_includes_aliases_for_ambiguous_symbols():
    prx_query = pubmed_gene_query("PRX")
    ackr1_query = pubmed_gene_query("ACKR1")

    assert "PRX[Gene]" in prx_query
    assert "periaxin" in prx_query.lower()
    assert "ACKR1[Gene]" in ackr1_query
    assert "DARC" in ackr1_query


def test_endothelial_signature_is_recognized():
    ui = UserInput(genes=["VWA1", "PLVAP", "PRX", "ACKR1"], tissue="Human lung", disease="IPF")
    mr = run_marker_agent(ui)
    top = mr.candidates[0]

    assert top.cell_type in {"Endothelial", "Pericyte"}
    assert any(c.cell_type == "Endothelial" for c in mr.candidates)
