"""Tests for the LLM-first evidence extractor (mock LLM, no network)."""
import pytest
from maple.models import AnalysisInput, RetrievalResult, RetrievedPaper
from maple.runtime.evidence_agent import run_evidence_agent
from maple.extraction.schemas import _LLMEvidenceOutput, _LLMEvidenceRow


def _make_paper(pmid, title, abstract, full_text=None, pmcid=None):
    import re
    year_match = re.search(r"\b(20\d\d)\b", abstract or title or "")
    year = int(year_match.group(1)) if year_match else 2023
    return RetrievedPaper(
        pmid=pmid,
        title=title,
        abstract=abstract,
        full_text=full_text,
        pmcid=pmcid,
        source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        publication_year=year,
        publication_date=str(year),
    )


def _make_retrieval(papers):
    return RetrievalResult(
        retrieved_papers=papers,
        total_searched=len(papers),
        total_after_dedup=len(papers),
    )


class MockLLM:
    """Returns a fixed set of extraction rows regardless of the prompt."""

    def __init__(self, rows):
        self._rows = rows

    def complete_json(self, system, user, schema):
        return _LLMEvidenceOutput(rows=[_LLMEvidenceRow(**r) for r in self._rows])


def test_llm_extracts_rows_from_abstract():
    inp = AnalysisInput(markers=["SFTPC", "SFTPA1"])
    abstract = (
        "Single-cell RNA sequencing revealed that SFTPC and SFTPA1 are "
        "highly expressed in alveolar type 2 cells in the human lung."
    )
    snippet = "SFTPC and SFTPA1 are highly expressed in alveolar type 2 cells"
    llm = MockLLM([{
        "celltype_label": "alveolar type 2 cell",
        "normalized_label": "alveolar type 2 cell",
        "matched_user_genes": ["SFTPC", "SFTPA1"],
        "evidence_snippet": snippet,
        "marker_specific": True,
        "specificity": "specific",
        "evidence_type": "direct_marker_celltype_assignment",
        "match_strength": "High",
        "evidence_section": "abstract",
    }])
    result = run_evidence_agent(inp, _make_retrieval([_make_paper("111", "Lung atlas", abstract)]), llm=llm)
    assert len(result.evidence_rows) == 1
    row = result.evidence_rows[0]
    assert set(row.matched_user_genes) == {"SFTPC", "SFTPA1"}
    assert row.celltype_label == "alveolar type 2 cell"
    assert row.marker_specific is True


def test_generic_lineage_label_is_not_dropped():
    """The old code discarded broad labels like 'fibroblast'; now they are kept."""
    inp = AnalysisInput(markers=["COL1A1", "DCN"])
    abstract = "COL1A1 and DCN were identified as markers of fibroblasts in the tissue."
    llm = MockLLM([{
        "celltype_label": "fibroblast",
        "normalized_label": "fibroblast",
        "matched_user_genes": ["COL1A1", "DCN"],
        "evidence_snippet": "COL1A1 and DCN were identified as markers of fibroblasts",
        "marker_specific": True,
        "specificity": "broad",
        "evidence_type": "direct_marker_celltype_assignment",
        "match_strength": "Medium",
        "evidence_section": "abstract",
    }])
    result = run_evidence_agent(inp, _make_retrieval([_make_paper("333", "Fibroblast markers", abstract)]), llm=llm)
    assert [r.celltype_label for r in result.evidence_rows] == ["fibroblast"]
    assert result.evidence_rows[0].specificity == "broad"


def test_excludes_paper_without_user_genes():
    inp = AnalysisInput(markers=["COL1A1", "POSTN"])
    abstract = "Macrophages play a role in fibrosis via TGF-beta signaling."
    llm = MockLLM([])  # never consulted because genes are absent
    result = run_evidence_agent(inp, _make_retrieval([_make_paper("222", "Fibrosis review", abstract)]), llm=llm)
    assert result.excluded_paper_count >= 1
    assert len(result.evidence_rows) == 0


def test_matched_genes_are_subset_of_user_genes():
    inp = AnalysisInput(markers=["COL1A1", "DCN"])
    abstract = "COL1A1 and DCN were identified as markers of fibroblast cells."
    # LLM hallucinates an extra gene not in the panel; it must be dropped.
    llm = MockLLM([{
        "celltype_label": "fibroblast",
        "normalized_label": "fibroblast",
        "matched_user_genes": ["COL1A1", "DCN", "ACTA2"],
        "evidence_snippet": "COL1A1 and DCN were identified as markers of fibroblast cells",
        "marker_specific": True,
        "specificity": "broad",
        "evidence_type": "direct_marker_celltype_assignment",
        "match_strength": "Medium",
        "evidence_section": "abstract",
    }])
    result = run_evidence_agent(inp, _make_retrieval([_make_paper("444", "Fibroblasts", abstract)]), llm=llm)
    for row in result.evidence_rows:
        for gene in row.matched_user_genes:
            assert gene in inp.markers
        assert row.number_of_user_genes_found == len(row.matched_user_genes)


def test_hallucinated_snippet_is_rejected():
    inp = AnalysisInput(markers=["COL1A1", "DCN"])
    abstract = "COL1A1 and DCN were identified as markers of fibroblasts."
    # Snippet text does not appear in the paper -> rejected as ungrounded.
    llm = MockLLM([{
        "celltype_label": "hepatocyte",
        "normalized_label": "hepatocyte",
        "matched_user_genes": ["COL1A1", "DCN"],
        "evidence_snippet": "COL1A1 and DCN define zonated hepatocytes in the liver lobule",
        "marker_specific": True,
        "specificity": "specific",
        "evidence_type": "direct_marker_celltype_assignment",
        "match_strength": "High",
        "evidence_section": "abstract",
    }])
    result = run_evidence_agent(inp, _make_retrieval([_make_paper("555", "Fibroblasts", abstract)]), llm=llm)
    assert len(result.evidence_rows) == 0


def test_no_llm_extracts_nothing():
    inp = AnalysisInput(markers=["SFTPC", "SFTPA1"])
    abstract = "SFTPC and SFTPA1 mark alveolar type 2 cells."
    result = run_evidence_agent(inp, _make_retrieval([_make_paper("111", "Lung", abstract)]), llm=None)
    assert result.evidence_rows == []
    assert any("LLM extraction unavailable" in n for n in result.audit_notes)


_BASALOID_PASSAGE = (
    "Among epithelial cells we identified aberrant basaloid cells. "
    "These cells express the basal cell markers TP63 and KRT17. "
    "They also express EMT markers VIM and CDH2, and senescence genes CDKN1A and CDKN2A."
)


def test_multisentence_panel_merged_into_one_row():
    """All genes a paper attributes to one cell type across sentences stay in one row."""
    inp = AnalysisInput(markers=["TP63", "CDH2", "CDKN1A", "CDKN2A", "KRT17", "VIM"])
    llm = MockLLM([{
        "celltype_label": "aberrant basaloid cells",
        "normalized_label": "aberrant basaloid cells",
        "matched_user_genes": ["TP63", "KRT17", "VIM", "CDH2", "CDKN1A", "CDKN2A"],
        "evidence_snippet": _BASALOID_PASSAGE,
        "marker_specific": True,
        "specificity": "specific",
        "evidence_type": "direct_marker_celltype_assignment",
        "match_strength": "High",
        "evidence_section": "abstract",
    }])
    result = run_evidence_agent(inp, _make_retrieval([_make_paper("1", "IPF", _BASALOID_PASSAGE)]), llm=llm)
    assert len(result.evidence_rows) == 1
    row = result.evidence_rows[0]
    assert set(row.matched_user_genes) == set(inp.markers)
    assert row.number_of_user_genes_found == 6


def test_gene_not_in_quote_is_dropped_but_row_kept():
    """A listed gene missing from the quoted passage is dropped; the row survives."""
    inp = AnalysisInput(markers=["TP63", "CDH2", "CDKN1A", "CDKN2A", "KRT17", "VIM"])
    # Exact sub-quote of the passage that stops at CDKN1A (so CDKN2A isn't covered).
    snippet_missing_cdkn2a = (
        "These cells express the basal cell markers TP63 and KRT17. "
        "They also express EMT markers VIM and CDH2, and senescence genes CDKN1A"
    )
    llm = MockLLM([{
        "celltype_label": "aberrant basaloid cells",
        "normalized_label": "aberrant basaloid cells",
        "matched_user_genes": ["TP63", "KRT17", "VIM", "CDH2", "CDKN1A", "CDKN2A"],
        "evidence_snippet": snippet_missing_cdkn2a,
        "marker_specific": True,
        "specificity": "specific",
        "evidence_type": "direct_marker_celltype_assignment",
        "match_strength": "High",
        "evidence_section": "abstract",
    }])
    result = run_evidence_agent(inp, _make_retrieval([_make_paper("1", "IPF", _BASALOID_PASSAGE)]), llm=llm)
    assert len(result.evidence_rows) == 1
    genes = set(result.evidence_rows[0].matched_user_genes)
    assert genes == {"TP63", "KRT17", "VIM", "CDH2", "CDKN1A"}  # CDKN2A dropped (not in quote)


def test_no_marker_rules_in_evidence_agent():
    """Evidence agent must not import marker_agent or load marker_rules.json."""
    import maple.runtime.evidence_agent as mod
    import inspect
    import re
    source = inspect.getsource(mod)
    assert not re.search(r"^\s*(import|from)\s+.*marker_agent", source, re.MULTILINE)
    assert not re.search(r"open\s*\(.*marker_rules", source)
    assert not re.search(r"json\.load.*marker_rules", source)


def test_discovery_queries_have_no_hardcoded_tissue_terms():
    """Generic discovery queries only — no lung/IPF/basaloid/AT0/vascular priors."""
    from maple.literature.pubmed import _build_simple_queries

    inp = AnalysisInput(markers=["TP63", "CDH2", "CDKN1A", "CDKN2A", "KRT17", "VIM"])
    queries = _build_simple_queries(inp)
    combo_queries = [q for q in queries if q.query_type == "panel_combo"]
    pair_queries = [q for q in queries if q.query_type == "panel_pair"]
    assert combo_queries
    assert pair_queries
    blob = " ".join(q.query.lower() for q in queries)
    for banned in ("basaloid", "aberrant", "distal lung", "at0", "aerocyte", "pulmonary", "kupffer"):
        assert banned not in blob


def test_fulltext_discovery_adds_paper_before_abstract_ranking(monkeypatch):
    """OA full-text discovery still retrieves body-text evidence via gene queries."""
    from models.schemas import PubMedPaper
    from maple.literature import pubmed as pubmed_mod

    body_text = "TP63 and KRT17 mark a distinct epithelial population in this study."

    class EmptyPubMedService:
        def retrieve_for_queries(self, queries, papers_per_query=5):
            return {}, []

    class FakeEuropePMCService:
        def search_open_access(self, query, max_results=3, open_access_only=True):
            if "TP63" not in query:
                return []
            return [
                PubMedPaper(
                    pmid="32832599",
                    pmcid="PMC7439502",
                    title="A single-cell study of an epithelial population",
                    journal="Science Advances",
                    year=2020,
                    abstract="We profiled epithelial cells.",
                    pubmed_url="https://pubmed.ncbi.nlm.nih.gov/32832599/",
                    is_open_access=True,
                )
            ]

        def full_text_for_paper(self, paper):
            return ""

    class FakeNCBIPMCService:
        def full_text_for_pmcid(self, pmcid):
            assert pmcid == "PMC7439502"
            return body_text

    import services.pubmed_service as pubmed_service_mod
    import services.fulltext_service as fulltext_service_mod
    import services.ncbi_pmc_service as ncbi_pmc_service_mod

    monkeypatch.setattr(pubmed_service_mod, "PubMedService", EmptyPubMedService)
    monkeypatch.setattr(fulltext_service_mod, "EuropePMCService", FakeEuropePMCService)
    monkeypatch.setattr(ncbi_pmc_service_mod, "NCBIPMCService", FakeNCBIPMCService)
    monkeypatch.setattr(pubmed_mod.cfg, "ENABLE_FULLTEXT", True)
    monkeypatch.setattr(pubmed_mod.cfg, "ENABLE_FULLTEXT_DISCOVERY", True)
    monkeypatch.setattr(pubmed_mod.cfg, "FULLTEXT_DISCOVERY_MAX_PAPERS", 3)

    result = pubmed_mod.retrieve_papers(
        AnalysisInput(markers=["TP63", "CDH2", "CDKN1A", "CDKN2A", "KRT17", "VIM"])
    )

    assert any(p.pmid == "32832599" and p.full_text for p in result.retrieved_papers)
    assert any("Full-text discovery" in note and "added 1" in note for note in result.audit_notes)


def test_fulltext_discovery_updates_existing_pubmed_record(monkeypatch):
    """If PubMed finds metadata first, full-text discovery must enrich it in place."""
    from models.schemas import PubMedPaper
    from maple.literature import pubmed as pubmed_mod

    body_text = "TP63 and KRT17 mark a distinct epithelial population in this study."
    paper = PubMedPaper(
        pmid="32832599",
        pmcid="",
        title="A single-cell study of an epithelial population",
        journal="Science Advances",
        year=2020,
        abstract="We profiled epithelial cells.",
        pubmed_url="https://pubmed.ncbi.nlm.nih.gov/32832599/",
    )

    class PubMedWithMetadata:
        def retrieve_for_queries(self, queries, papers_per_query=5):
            return {"KRT17": [paper]}, []

    class FakeEuropePMCService:
        def search_open_access(self, query, max_results=3, open_access_only=True):
            return [paper.model_copy(update={"pmcid": "PMC7439502", "is_open_access": True})]

        def full_text_for_paper(self, p):
            return ""

    class FakeNCBIPMCService:
        def full_text_for_pmcid(self, pmcid):
            return body_text

    import services.pubmed_service as pubmed_service_mod
    import services.fulltext_service as fulltext_service_mod
    import services.ncbi_pmc_service as ncbi_pmc_service_mod

    monkeypatch.setattr(pubmed_service_mod, "PubMedService", PubMedWithMetadata)
    monkeypatch.setattr(fulltext_service_mod, "EuropePMCService", FakeEuropePMCService)
    monkeypatch.setattr(ncbi_pmc_service_mod, "NCBIPMCService", FakeNCBIPMCService)
    monkeypatch.setattr(pubmed_mod.cfg, "ENABLE_FULLTEXT", True)
    monkeypatch.setattr(pubmed_mod.cfg, "ENABLE_FULLTEXT_DISCOVERY", True)
    monkeypatch.setattr(pubmed_mod.cfg, "FULLTEXT_DISCOVERY_MAX_PAPERS", 3)

    result = pubmed_mod.retrieve_papers(
        AnalysisInput(markers=["TP63", "CDH2", "CDKN1A", "CDKN2A", "KRT17", "VIM"])
    )

    rows = [p for p in result.retrieved_papers if p.pmid == "32832599"]
    assert len(rows) == 1
    assert rows[0].pmcid == "PMC7439502"
    assert body_text in rows[0].full_text
    assert any("updated 1" in note for note in result.audit_notes)
