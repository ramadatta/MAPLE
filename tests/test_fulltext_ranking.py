"""Tests for full-text gene counting in paper ranking and Europe PMC parsing."""

import logging
import requests

from agents.paper_ranking import rank_papers_by_marker_coverage
from models.schemas import PubMedPaper
from services.ncbi_pmc_service import NCBIPMCService
from services.fulltext_service import EuropePMCService

GENES = ["TP63", "CDH2", "CDKN1A", "CDKN2A", "VIM", "KRT17", "LAMB3", "LAMC2"]


def test_paper_from_epmc_result_parses_core_fields():
    res = {
        "pmid": "32832599",
        "pmcid": "PMC7439502",
        "title": "Aberrant basaloid cells in IPF.",
        "journalTitle": "Sci Adv",
        "pubYear": "2020",
        "abstractText": "We profiled KRT17+ cells.",
        "isOpenAccess": "Y",
    }
    paper = EuropePMCService._paper_from_epmc_result(res)
    assert paper.pmid == "32832599"
    assert paper.pmcid == "PMC7439502"
    assert paper.year == 2020
    assert paper.is_open_access is True
    assert paper.title == "Aberrant basaloid cells in IPF"


def test_full_text_for_paper_prefers_pmcid(monkeypatch):
    svc = EuropePMCService()
    calls = {"pmcid": None, "pmid": None}
    monkeypatch.setattr(svc, "full_text_for_pmcid", lambda p: calls.__setitem__("pmcid", p) or "FT")
    monkeypatch.setattr(svc, "full_text_for_pmid", lambda p: calls.__setitem__("pmid", p) or "")
    paper = PubMedPaper(pmid="1", title="t", pmcid="PMC9", pubmed_url="")
    assert svc.full_text_for_paper(paper) == "FT"
    assert calls["pmcid"] == "PMC9" and calls["pmid"] is None


def test_ncbi_full_text_refetches_empty_cache(monkeypatch):
    svc = NCBIPMCService()
    cache = {"value": ""}
    fetch_calls = {"n": 0}

    monkeypatch.setattr(svc.cache, "get", lambda namespace, key: cache["value"])

    def fake_fetch(pmcid):
        fetch_calls["n"] += 1
        return "FULL TEXT"

    def fake_set(namespace, key, value):
        cache["value"] = value

    monkeypatch.setattr(svc, "_fetch_full_text_xml", fake_fetch)
    monkeypatch.setattr(svc.cache, "set", fake_set)

    assert svc.full_text_for_pmcid("PMC9") == "FULL TEXT"
    assert fetch_calls["n"] == 1
    assert cache["value"] == "FULL TEXT"


def test_strip_xml_flattens_jats():
    xml = "<article><body><p>KRT17 and TP63 in <i>basaloid</i> cells.</p></body></article>"
    text = EuropePMCService._strip_xml(xml)
    assert "KRT17" in text and "TP63" in text and "basaloid" in text


def test_strip_xml_handles_malformed():
    assert EuropePMCService._strip_xml("<not valid xml") == ""


def test_epmc_fulltext_404_logs_debug_not_warning(monkeypatch, caplog):
    svc = EuropePMCService()

    def fake_get(*args, **kwargs):
        raise requests.HTTPError("404 Client Error: Not Found")

    monkeypatch.setattr("services.fulltext_service.requests.get", fake_get)

    with caplog.at_level(logging.DEBUG, logger="services.fulltext_service"):
        assert svc._get("https://example.org/PMC/PMC1/fullTextXML") is None

    assert any("full text unavailable" in rec.message for rec in caplog.records)
    assert not any(rec.levelno >= logging.WARNING for rec in caplog.records)


def test_full_text_lifts_coverage_above_abstract():
    paper = PubMedPaper(
        pmid="32832599",
        title="Aberrant basaloid cells in IPF",
        abstract="We profiled KRT17+ cells.",  # abstract names only KRT17
        year=2020,
        pubmed_url="https://pubmed.ncbi.nlm.nih.gov/32832599/",
    )
    papers = {"KRT17": [paper]}

    # Abstract-only: coverage is just KRT17.
    abstract_only = rank_papers_by_marker_coverage(GENES, papers)
    assert abstract_only[0].marker_count == 1
    assert abstract_only[0].full_text_used is False

    # Full text mentions the whole panel.
    full_text = "TP63 CDH2 CDKN1A CDKN2A VIM KRT17 LAMB3 LAMC2 aberrant basaloid"
    ranked = rank_papers_by_marker_coverage(GENES, papers, text_fn=lambda p: full_text)
    assert ranked[0].marker_count == 8
    assert ranked[0].full_text_used is True
    assert ranked[0].cell_type == "Aberrant basaloid"


def test_max_full_text_cap_limits_fetches():
    papers = {
        f"g{i}": [
            PubMedPaper(
                pmid=str(i),
                title="t",
                abstract="KRT17",
                year=2020,
                pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/{i}/",
            )
        ]
        for i in range(5)
    }
    calls = {"n": 0}

    def counting_text_fn(_paper):
        calls["n"] += 1
        return "TP63 CDH2"

    rank_papers_by_marker_coverage(GENES, papers, text_fn=counting_text_fn, max_full_text=2)
    assert calls["n"] == 2


def test_text_fn_failure_falls_back_to_abstract():
    paper = PubMedPaper(
        pmid="1", title="t", abstract="KRT17 TP63", year=2020,
        pubmed_url="https://pubmed.ncbi.nlm.nih.gov/1/",
    )

    def boom(_paper):
        raise RuntimeError("network down")

    ranked = rank_papers_by_marker_coverage(GENES, {"KRT17": [paper]}, text_fn=boom)
    assert ranked[0].marker_count == 2  # KRT17, TP63 from abstract
    assert ranked[0].full_text_used is False
