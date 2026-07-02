"""Tests for Scholar (Smithery) + preprint discovery — no network, no mcp import."""
import pytest

from maple.models import AnalysisInput, RetrievedPaper
from models.schemas import PubMedPaper
from services.scholar_service import ScholarService, normalize_scholar_results
from services.preprint_service import PreprintService, _titles_match


# --- Scholar result normalization -------------------------------------------

def test_normalize_scholar_serpapi_shape():
    raw = {
        "organic_results": [
            {
                "title": "Ex vivo tissue perturbations reveal lung fibrogenesis",
                "link": "https://www.science.org/doi/10.1126/scitranslmed.adh0908",
                "snippet": "VWA1+/PLVAP+ ectopic ECs express COL4A1 and COL4A2 ...",
                "publication_info": {"summary": "NJ Lang, J Gote-Schniering - Sci Transl Med, 2023"},
                "inline_links": {"cited_by": {"total": 74}},
                "resources": [{"link": "https://example.org/paper.pdf", "file_format": "PDF"}],
            }
        ]
    }
    hits = normalize_scholar_results(raw)
    assert len(hits) == 1
    h = hits[0]
    assert h["year"] == 2023
    assert h["pdf_url"].endswith(".pdf")
    assert h["citations"] == 74
    assert "VWA1" in h["snippet"]


def test_normalize_scholar_accepts_json_string_and_ignores_junk():
    assert normalize_scholar_results("not json") == []
    assert normalize_scholar_results({"results": [{"no_title": 1}]}) == []


def test_scholar_search_fails_soft(monkeypatch):
    svc = ScholarService("https://example/mcp", api_key="")

    async def _boom(query, num):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(svc, "_acall", _boom)
    assert svc.search("VWA1 PLVAP", 5) == []


# --- Preprint service --------------------------------------------------------

class _Resp:
    def __init__(self, json_data=None, text=""):
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def test_titles_match():
    assert _titles_match("Human distal lung maps and lineage hierarchies", "Human distal lung maps and lineage hierarchies reveal")
    assert not _titles_match("Kupffer cells in liver", "Endothelial cells in pancreas")


def test_search_preprints_keeps_only_biorxiv_dois(monkeypatch):
    svc = PreprintService()
    payload = {"resultList": {"result": [
        {"doi": "10.1101/2023.01.16.524219", "title": "A preprint.", "pubYear": "2023",
         "abstractText": "abstract", "bookOrReportDetails": {"publisher": "bioRxiv"}},
        {"doi": "10.1038/s41586-020-0", "title": "Not a preprint.", "pubYear": "2020"},
    ]}}
    monkeypatch.setattr(svc, "_get", lambda url, params=None: _Resp(json_data=payload))
    papers = svc.search_preprints("VWA1 AND PLVAP", max_results=10)
    assert len(papers) == 1
    assert papers[0].doi == "10.1101/2023.01.16.524219"


def test_resolve_preprint_doi_uses_loose_query_and_strips_html(monkeypatch):
    """Published and preprint titles differ slightly; a loose query + fuzzy match wins."""
    svc = PreprintService()
    captured = {}
    payload = {"resultList": {"result": [
        {"doi": "10.1101/2023.01.16.524219",
         "title": "<i>Ex vivo</i> tissue perturbations coupled to single cell RNA-seq reveal multi-lineage cell circuit dynamics in human lung fibrogenesis",
         "pubYear": "2023", "bookOrReportDetails": {"publisher": "bioRxiv"}},
    ]}}

    def fake_get(url, params=None):
        captured["query"] = (params or {}).get("query", "")
        return _Resp(json_data=payload)

    monkeypatch.setattr(svc, "_get", fake_get)
    doi = svc.resolve_preprint_doi(
        "Ex vivo tissue perturbations coupled to single-cell RNA-seq reveal "
        "multilineage cell circuit dynamics in human lung fibrogenesis"
    )
    assert doi == "10.1101/2023.01.16.524219"
    assert '"' not in captured["query"]  # not an exact-phrase query


def test_full_text_for_doi_parses_jats(monkeypatch):
    svc = PreprintService()
    details = {"collection": [{"jatsxml": "https://www.biorxiv.org/x.source.xml"}]}
    jats = "<article><body><p>VWA1 and PLVAP mark ectopic ECs.</p></body></article>"

    def fake_get(url, params=None):
        if url.startswith("https://api.biorxiv.org"):
            return _Resp(json_data=details)
        return _Resp(text=jats)

    monkeypatch.setattr(svc, "_get", fake_get)
    text = svc.full_text_for_doi("10.1101/2023.01.16.524219")
    assert "VWA1 and PLVAP mark ectopic ECs" in text


# --- Scholar discovery wiring ------------------------------------------------

def test_scholar_discovery_adds_fulltext_paper(monkeypatch):
    from maple.literature import pubmed as pubmed_mod

    class FakeScholar:
        def __init__(self, *a, **k):
            pass

        def search(self, query, max_results):
            return [{"title": "Ex vivo lung fibrogenesis multilineage circuit",
                     "link": "https://doi.org/10.1126/scitranslmed.adh0908",
                     "snippet": "VWA1 PLVAP COL4A1 COL4A2", "year": 2023, "citations": 74,
                     "pdf_url": ""}]

    class FakeEPMC:
        def search_open_access(self, query, max_results=1, open_access_only=True):
            return []  # not in PMC -> force preprint fallback

    class FakeNCBI:
        def full_text_for_pmcid(self, pmcid):
            return ""

    class FakePreprint:
        def resolve_preprint_doi(self, title):
            return "10.1101/2023.01.16.524219"

        def full_text_for_doi(self, doi):
            return "VWA1+/PLVAP+ ectopic ECs express COL4A1 and COL4A2 in lung fibrosis."

    import services.scholar_service as scholar_mod
    import services.fulltext_service as fulltext_mod
    import services.ncbi_pmc_service as ncbi_mod
    import services.preprint_service as preprint_mod

    monkeypatch.setattr(scholar_mod, "ScholarService", FakeScholar)
    monkeypatch.setattr(fulltext_mod, "EuropePMCService", FakeEPMC)
    monkeypatch.setattr(ncbi_mod, "NCBIPMCService", FakeNCBI)
    monkeypatch.setattr(preprint_mod, "PreprintService", FakePreprint)
    monkeypatch.setattr(pubmed_mod.cfg, "ENABLE_SCHOLAR", True)

    papers: list[RetrievedPaper] = []
    added = pubmed_mod._add_scholar_discovery_papers(
        papers, set(), set(), AnalysisInput(markers=["VWA1", "PLVAP", "COL4A1", "COL4A2"]), []
    )
    assert added == 1
    assert papers[0].full_text and "COL4A1 and COL4A2" in papers[0].full_text
    assert papers[0].doi == "10.1101/2023.01.16.524219"


def test_openalex_parses_work_ids_and_abstract(monkeypatch):
    from services.openalex_service import OpenAlexService, _reconstruct_abstract

    assert _reconstruct_abstract({"VWA1": [0], "and": [1], "PLVAP": [2]}) == "VWA1 and PLVAP"

    svc = OpenAlexService(mailto="x@example.org")
    payload = {"results": [{
        "title": "Ex vivo lung fibrogenesis multilineage circuit",
        "doi": "https://doi.org/10.1126/scitranslmed.adh0908",
        "publication_year": 2023,
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/38055803",
                "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/"},
        "open_access": {"is_oa": False},
        "abstract_inverted_index": {"ectopic": [0], "endothelial": [1]},
    }]}
    monkeypatch.setattr(svc, "_get", lambda params: _Resp(json_data=payload))
    papers = svc.search_fulltext("VWA1 PLVAP COL4A1 COL4A2", 15)
    assert len(papers) == 1
    p = papers[0]
    assert p.pmid == "38055803"
    assert p.pmcid == "PMC123"
    assert p.doi == "10.1126/scitranslmed.adh0908"
    assert p.abstract == "ectopic endothelial"


def test_openalex_discovery_adds_preprint_fulltext(monkeypatch):
    from maple.literature import pubmed as pubmed_mod

    class FakeOpenAlex:
        def __init__(self, *a, **k):
            pass

        def search_fulltext(self, query, max_results):
            return [PubMedPaper(pmid="38055803", title="Ex vivo lung fibrogenesis",
                                doi="10.1126/scitranslmed.adh0908", year=2023)]

    class FakeEPMC:
        def full_text_for_pmcid(self, pmcid):
            return ""

    class FakeNCBI:
        def full_text_for_pmcid(self, pmcid):
            return ""

    class FakePreprint:
        def full_text_for_doi(self, doi):
            return ""

        def full_text_by_title(self, title):
            return "VWA1+/PLVAP+ ectopic ECs express COL4A1 and COL4A2."

    import services.openalex_service as oa_mod
    import services.fulltext_service as ft_mod
    import services.ncbi_pmc_service as ncbi_mod
    import services.preprint_service as pp_mod

    monkeypatch.setattr(oa_mod, "OpenAlexService", FakeOpenAlex)
    monkeypatch.setattr(ft_mod, "EuropePMCService", FakeEPMC)
    monkeypatch.setattr(ncbi_mod, "NCBIPMCService", FakeNCBI)
    monkeypatch.setattr(pp_mod, "PreprintService", FakePreprint)
    monkeypatch.setattr(pubmed_mod.cfg, "ENABLE_OPENALEX", True)

    papers = []
    added = pubmed_mod._add_openalex_discovery_papers(
        papers, set(), set(), AnalysisInput(markers=["VWA1", "PLVAP", "COL4A1", "COL4A2"]), []
    )
    assert added == 1
    assert "COL4A1 and COL4A2" in papers[0].full_text
    assert papers[0].pmid == "38055803"


def test_openalex_skips_already_retrieved(monkeypatch):
    from maple.literature import pubmed as pubmed_mod

    class FakeOpenAlex:
        def __init__(self, *a, **k):
            pass

        def search_fulltext(self, query, max_results):
            return [PubMedPaper(pmid="999", title="Already have it", doi="10.1/x")]

    import services.openalex_service as oa_mod
    monkeypatch.setattr(oa_mod, "OpenAlexService", FakeOpenAlex)
    monkeypatch.setattr(pubmed_mod.cfg, "ENABLE_OPENALEX", True)

    papers = []
    added = pubmed_mod._add_openalex_discovery_papers(
        papers, {"999"}, set(), AnalysisInput(markers=["VWA1", "PLVAP"]), []
    )
    assert added == 0 and papers == []


def test_scholar_discovery_disabled_by_flag(monkeypatch):
    from maple.literature import pubmed as pubmed_mod
    monkeypatch.setattr(pubmed_mod.cfg, "ENABLE_SCHOLAR", False)
    papers: list[RetrievedPaper] = []
    added = pubmed_mod._add_scholar_discovery_papers(
        papers, set(), set(), AnalysisInput(markers=["VWA1", "PLVAP"]), []
    )
    assert added == 0 and papers == []
