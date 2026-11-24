import pytest
import requests

from maples.runtime import activity
from maples.services import host_health


def setup_function():
    host_health.reset()


def teardown_function():
    host_health.reset()


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_host_is_skipped_only_after_repeated_unreachable_errors():
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    for _ in range(host_health.FAILS_TO_TRIP - 1):
        host_health.record_failure(url)
    assert not host_health.is_down(url)
    host_health.record_failure(url)
    assert host_health.is_down(url)
    assert not host_health.is_down("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi")


def test_slow_answers_are_not_outages():
    assert host_health.is_outage_error(requests.ConnectionError())
    assert host_health.is_outage_error(requests.ConnectTimeout())  # could not connect at all
    assert not host_health.is_outage_error(requests.ReadTimeout())  # connected, just slow
    assert not host_health.is_outage_error(requests.HTTPError("503"))


def test_patient_get_retries_busy_and_slow_answers(monkeypatch):
    answers = [requests.ReadTimeout("slow"), _Resp(503), _Resp(200)]

    def fake_get(*a, **k):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(host_health.requests, "get", fake_get)
    monkeypatch.setattr(host_health.time, "sleep", lambda s: None)
    resp = host_health.patient_get("https://www.ebi.ac.uk/x")
    assert resp.status_code == 200
    assert not answers
    assert not host_health.is_down("https://www.ebi.ac.uk/x")


def test_patient_get_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(host_health.requests, "get", lambda *a, **k: _Resp(503))
    monkeypatch.setattr(host_health.time, "sleep", lambda s: None)
    with pytest.raises(requests.HTTPError):
        host_health.patient_get("https://www.ebi.ac.uk/x")


def test_europe_pmc_service_stops_calling_an_unreachable_host(monkeypatch):
    from maples.services.fulltext_service import EuropePMCService

    calls = []

    def fake_get(*a, **k):
        calls.append(a)
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(host_health.requests, "get", fake_get)
    svc = EuropePMCService()
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    for _ in range(6):
        assert svc._get(url, {}) is None
    assert len(calls) == host_health.FAILS_TO_TRIP


def test_activity_report_reaches_the_reporter_and_is_silent_without_one():
    seen = []
    activity.report("europepmc", searches_done=1)  # no reporter: nothing happens
    token = activity.set_reporter(lambda src, f: seen.append((src, f)))
    try:
        activity.report("europepmc", searches_done=2, searches_total=4)
    finally:
        activity._reporter.reset(token)
    assert seen == [("europepmc", {"searches_done": 2, "searches_total": 4})]


def test_patient_get_retries_a_slow_answer_only_once(monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        raise requests.ReadTimeout("slow")

    monkeypatch.setattr(host_health.requests, "get", fake_get)
    monkeypatch.setattr(host_health.time, "sleep", lambda s: None)
    with pytest.raises(requests.ReadTimeout):
        host_health.patient_get("https://www.ebi.ac.uk/x")
    assert len(calls) == 2
