"""Tests for Europe PMC / NCBI PMC full-text fetching and parsing."""

import logging
import requests

from maples.services.schemas import PubMedPaper
from maples.services.ncbi_pmc_service import NCBIPMCService
from maples.services.fulltext_service import EuropePMCService



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

    monkeypatch.setattr("maples.services.fulltext_service.requests.get", fake_get)

    with caplog.at_level(logging.DEBUG, logger="maples.services.fulltext_service"):
        assert svc._get("https://example.org/PMC/PMC1/fullTextXML") is None

    assert any("full text unavailable" in rec.message for rec in caplog.records)
    assert not any(rec.levelno >= logging.WARNING for rec in caplog.records)
