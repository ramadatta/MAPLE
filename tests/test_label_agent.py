"""Tests for grouping different names of the same cell population."""
from maples.models import EvidenceRow
from maples.runtime.label_agent import _MergeGroup, _MergeOutput, label_key, run_label_agent


def _row(label, genes=("KRT17",), pmid="1", norm=""):
    return EvidenceRow(
        pmid=pmid, paper_title="t", celltype_label=label, normalized_label=norm,
        matched_user_genes=list(genes), number_of_user_genes_found=len(genes),
        evidence_snippet=f"{label} expressed " + ", ".join(genes),
    )


def test_label_key_ignores_formatting():
    assert label_key("KRT5 - /KRT17 + epithelial cells") == label_key("KRT5-/KRT17+ epithelial cell")
    assert label_key("Aberrant Basaloid cells") == label_key("aberrant basaloid cell")
    assert label_key("Alveolar type II cells") == label_key("alveolar type 2 cell")
    assert label_key("Airway basal cells (ABC)") == label_key("airway basal cells")
    assert label_key("basaloid-like cells") == label_key("basaloid like cells")


def test_label_key_keeps_marker_signs():
    assert label_key("KRT5+ basal cells") != label_key("KRT5- basal cells")


def test_format_matching_without_llm():
    rows = [_row("Aberrant Basaloid cells", pmid="1"), _row("aberrant basaloid cell", pmid="2"),
            _row("Fibroblasts", pmid="3")]
    groups, _notes = run_label_agent(rows)
    assert rows[0].label_group == rows[1].label_group == "Aberrant Basaloid cells"
    assert rows[2].label_group == "Fibroblasts"
    assert len(groups) == 1 and set(groups[0].members) == {"Aberrant Basaloid cells", "aberrant basaloid cell"}


class FakeLLM:
    def __init__(self, output):
        self.output = output
        self.prompts = []

    def complete_json(self, system, user, schema):
        self.prompts.append(user)
        return self.output


def test_llm_merges_synonyms_under_the_most_used_paper_name():
    rows = [_row("AT2 cells", pmid="1"), _row("Alveolar type II epithelial cells", pmid="2"),
            _row("Basal cells", pmid="3"), _row("alveolar type II epithelial cell", pmid="5")]
    llm = FakeLLM(_MergeOutput(groups=[_MergeGroup(name="AT2 cells", members=[1, 2])]))
    groups, _ = run_label_agent(rows, llm)
    # Two of three quotes use the "alveolar type II" spelling, so that name wins.
    assert rows[0].label_group == rows[1].label_group == rows[3].label_group == "Alveolar type II epithelial cells"
    assert rows[2].label_group == "Basal cells"
    assert groups[0].row_count == 3


def test_llm_invented_name_is_replaced_and_bad_indices_ignored():
    rows = [_row("AT2 cells", pmid="1"), _row("AT2 cells", pmid="4"), _row("Type 2 pneumocytes", pmid="2")]
    llm = FakeLLM(_MergeOutput(groups=[_MergeGroup(name="Made-up name", members=[1, 2, 99])]))
    run_label_agent(rows, llm)
    assert {r.label_group for r in rows} == {"AT2 cells"}


def test_llm_failure_falls_back_to_format_matching():
    class Boom:
        def complete_json(self, **_):
            raise RuntimeError("down")

    rows = [_row("Basal cells"), _row("basal cell", pmid="2"), _row("Fibroblasts", pmid="3")]
    _groups, notes = run_label_agent(rows, Boom())
    assert rows[0].label_group == rows[1].label_group
    assert any("skipped" in n for n in notes)


def test_llm_merges_never_cross_organ_systems():
    from maples.fields import normalize_context

    rows = [_row("Basal cells", pmid="1"), _row("Basal-like tumor cells", pmid="2"), _row("KRT5+ basal cells", pmid="3")]
    rows[0].field = normalize_context("lung", "IPF", "human")
    rows[1].field = normalize_context("bladder", "urothelial carcinoma", "human")
    rows[2].field = normalize_context("airway", "COPD", "human")
    llm = FakeLLM(_MergeOutput(groups=[_MergeGroup(members=[1, 2, 3])]))
    run_label_agent(rows, llm)
    assert rows[0].label_group == rows[2].label_group          # lung + airway: both respiratory
    assert rows[1].label_group == "Basal-like tumor cells"      # bladder kept apart
    assert "field: Lung · IPF · Human" in llm.prompts[0]
