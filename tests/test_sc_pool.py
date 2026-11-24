from types import SimpleNamespace

from maples import config as cfg
from maples.literature import pool
from maples.models import AnalysisInput, RetrievedPaper
from maples.runtime import evidence_agent


def test_aliases_merge_into_one_gene():
    groups = pool.gene_groups(["VEGFR3", "FLT4", "KDR", "CD34"])
    syms = [g[0] for g in groups]
    assert syms == ["FLT4", "KDR", "CD34"]
    assert groups[0][1] == ["VEGFR3", "FLT4"]          # the user's own names, kept together
    assert "VEGFR2" in groups[1][2]                    # KDR is also searched as VEGFR2


def test_query_needs_two_different_genes():
    assert pool.build_query(pool.gene_groups(["VEGFR3", "FLT4"])) == ""
    q = pool.build_query(pool.gene_groups(["FLT4", "CD34"]))
    assert "FLT4[tiab]" in q and "CD34[tiab]" in q and "single-cell" in q
    assert '"VEGFR-3"[tiab]' in q


def test_two_names_of_one_gene_do_not_count_as_two():
    groups = pool.gene_groups(["VEGFR3", "FLT4", "CD34"])
    assert pool.distinct_genes_in("VEGFR3 (FLT4) marks lymphatics", groups) == ["VEGFR3"]
    assert pool.distinct_genes_in("FLT4 and CD34 in single-cell data", groups) == ["VEGFR3", "CD34"]


def _paper(pmid, title, abstract=""):
    return RetrievedPaper(pmid=pmid, title=title, abstract=abstract)


def test_add_sc_pool_keeps_only_true_two_gene_papers(monkeypatch):
    monkeypatch.setattr(cfg, "ENABLE_SC_POOL", True)
    monkeypatch.setattr(pool, "_esearch_post", lambda backend, q, n: (3, ["1", "2", "3"]))
    pubs = {
        "2": SimpleNamespace(pmid="2", title="FLT4 and CD34 single-cell atlas"),
        "3": SimpleNamespace(pmid="3", title="VEGFR3/FLT4 in single cells"),  # one gene, two names
    }
    backend = SimpleNamespace(fetch_papers=lambda ids: [pubs[i] for i in ids if i in pubs])
    existing = [_paper("1", "CD34+ and FLT4+ cells by scRNA-seq")]
    seen = {"1"}
    facts = pool.add_sc_pool(
        existing, seen, AnalysisInput(markers=["FLT4", "CD34", "VEGFR3"]), [],
        SimpleNamespace(backend=backend), lambda pub: _paper(pub.pmid, pub.title),
    )
    assert facts["in_pubmed"] == 3 and facts["kept"] == 2 and facts["dropped"] == 1 and facts["new"] == 1
    assert [p.pmid for p in existing] == ["1", "2"]
    assert all(p.in_pool for p in existing)


def test_pool_reading_slots_and_skim(monkeypatch):
    monkeypatch.setattr(cfg, "SC_POOL_READ_MAX", 2)
    monkeypatch.setattr(cfg, "ENABLE_FULLTEXT", False)
    cands = [(_paper(str(i), f"paper {i}"), ["FLT4", "CD34"]) for i in range(5)]

    class FakeLLM:
        def complete_json(self, system, user, schema):
            return schema(relevant=[1, 3, 4])  # papers 0, 2, 3 tie the genes to a cell type

    notes = []
    chosen, screened, not_read = evidence_agent._choose_pool_papers(cands, ["FLT4", "CD34"], FakeLLM(), notes)
    assert [p.pmid for p, _ in chosen] == ["0", "2"]
    assert screened == ["1", "4"]
    assert not_read == ["3"]


def test_small_pool_is_read_without_skim(monkeypatch):
    monkeypatch.setattr(cfg, "SC_POOL_READ_MAX", 40)
    monkeypatch.setattr(cfg, "ENABLE_FULLTEXT", False)
    cands = [(_paper(str(i), f"paper {i}"), ["FLT4", "CD34"]) for i in range(3)]

    class NoCalls:
        def complete_json(self, *a, **k):
            raise AssertionError("no skim needed for a small pool")

    chosen, screened, not_read = evidence_agent._choose_pool_papers(cands, ["FLT4"], NoCalls(), [])
    assert len(chosen) == 3 and screened == [] and not_read == []
