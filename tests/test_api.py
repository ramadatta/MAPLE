"""Unit tests for the public maple API (no network — pipeline is monkeypatched)."""
import asyncio

import pytest

import maple.runtime.orchestrator as orch
from maple import annotate, annotate_async, annotate_marker_sets, AnnotationResult
from maple.models import (
    AnalysisInput,
    AnalysisState,
    ConsensusAlternative,
    ConsensusResult,
    EvidenceRow,
    ExtractionResult,
)


def _fake_state(markers, tissue=None, disease=None, species=None) -> AnalysisState:
    row = EvidenceRow(
        pmid="111",
        paper_title="Lung atlas",
        celltype_label="alveolar type 2 cell",
        matched_user_genes=list(markers[:2]),
        number_of_user_genes_found=min(2, len(markers)),
        evidence_snippet="... markers of alveolar type 2 cells ...",
    )
    return AnalysisState(
        input=AnalysisInput(
            markers=list(markers), tissue=tissue, disease=disease, species=species
        ),
        extraction=ExtractionResult(evidence_rows=[row]),
        consensus=ConsensusResult(
            consensus_label="alveolar type 2 cell",
            confidence="High",
            consensus_rationale="Supported by literature.",
            supporting_genes=list(markers),
            main_supporting_pmids=["111"],
            alternative_labels=[
                ConsensusAlternative(label="AT1 cell", reason_not_selected="fewer genes")
            ],
        ),
    )


@pytest.fixture(autouse=True)
def _no_env_llm(monkeypatch):
    """Never build a real LLM from environment during tests."""
    monkeypatch.setattr("maple.api._make_llm_from_env", lambda: None)


def _patch_pipeline(monkeypatch, capture=None, raise_for=None):
    async def fake(analysis_input, llm=None, progress_callback=None):
        if capture is not None:
            capture["input"] = analysis_input
        if raise_for is not None and set(analysis_input.markers) == set(raise_for):
            raise RuntimeError("boom")
        return _fake_state(
            analysis_input.markers,
            analysis_input.tissue,
            analysis_input.disease,
            analysis_input.species,
        )

    monkeypatch.setattr(orch, "run_maple_pipeline", fake)


def test_annotate_sync_maps_result(monkeypatch):
    _patch_pipeline(monkeypatch)
    res = annotate(["COL1A1", "COL3A1", "POSTN"], tissue="lung")
    assert isinstance(res, AnnotationResult)
    assert res.label == "alveolar type 2 cell"
    assert res.confidence == "High"
    assert len(res.evidence) == 1
    assert res.alternatives[0].label == "AT1 cell"
    assert res.run_metadata.marker_count == 3
    assert res.run_metadata.tissue == "lung"
    assert res.run_metadata.llm_used is False


def test_annotate_async_runs(monkeypatch):
    _patch_pipeline(monkeypatch)
    res = asyncio.run(annotate_async("SFTPC, SFTPA1, ABCA3"))
    assert res.label == "alveolar type 2 cell"
    assert res.run_metadata.marker_count == 3


def test_marker_normalization(monkeypatch):
    capture = {}
    _patch_pipeline(monkeypatch, capture=capture)
    annotate('["col1a1", "col3a1", "postn"]')  # python-list syntax, lowercase
    assert capture["input"].markers == ["COL1A1", "COL3A1", "POSTN"]


def test_empty_markers_raises(monkeypatch):
    _patch_pipeline(monkeypatch)
    with pytest.raises(ValueError):
        annotate("   ")


def test_serialization_excludes_raw_state_by_default(monkeypatch):
    _patch_pipeline(monkeypatch)
    res = annotate(["COL1A1", "COL3A1", "POSTN"], include_raw_state=True)
    assert res.raw_state is not None
    assert "raw_state" not in res.to_dict()
    assert "raw_state" in res.to_dict(include_raw=True)
    assert isinstance(res.to_json(), str)


def test_annotate_within_running_loop(monkeypatch):
    """Calling the sync annotate() from inside a running loop must not deadlock."""
    _patch_pipeline(monkeypatch)

    async def inner():
        return annotate(["COL1A1", "COL3A1", "POSTN"])

    res = asyncio.run(inner())
    assert res.label == "alveolar type 2 cell"


def test_annotate_marker_sets_with_failure_isolation(monkeypatch):
    _patch_pipeline(monkeypatch, raise_for={"BADGENE"})
    out = annotate_marker_sets(
        {
            "0": ["COL1A1", "DCN", "LUM"],
            "1": ["SFTPC", "ABCA3", "SFTPA1"],
            "2": ["BADGENE"],
        },
        tissue="lung",
    )
    assert set(out) == {"0", "1", "2"}
    assert out["0"].label == "alveolar type 2 cell"
    assert out["1"].label == "alveolar type 2 cell"
    # Group 2 failed but did not abort the batch.
    assert out["2"].warnings and "failed" in out["2"].warnings[0].lower()
