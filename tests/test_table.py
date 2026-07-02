"""Tests for evidence table rendering and pagination."""
import pytest
from tests.fixtures.sample_state import make_fibrosis_fibroblast_state
from maple.ui.table import (
    get_filtered_rows, get_sorted_rows, get_page, to_csv, render_evidence_table_html,
)
from maple.models import TableState, EvidenceRow


def test_pagination():
    state = make_fibrosis_fibroblast_state()
    # Add 25 fake rows to test pagination
    extra_rows = [
        EvidenceRow(
            pmid=str(i),
            paper_title=f"Paper {i}",
            celltype_label="test cell",
            matched_user_genes=["COL1A1"],
            number_of_user_genes_found=1,
            evidence_snippet=f"snippet {i}",
            match_strength="Low",
        )
        for i in range(25)
    ]
    state.extraction.evidence_rows.extend(extra_rows)

    all_rows = get_filtered_rows(state)
    sorted_rows = get_sorted_rows(all_rows, state.table_state)
    page_rows, total_pages, total_rows = get_page(sorted_rows, state.table_state)

    assert total_rows == len(state.extraction.evidence_rows)
    assert len(page_rows) <= state.table_state.page_size


def test_search_filter():
    state = make_fibrosis_fibroblast_state()
    state.table_state.search_query = "fibroblast"
    rows = get_filtered_rows(state)
    for row in rows:
        text = (
            row.celltype_label + row.paper_title + " ".join(row.matched_user_genes)
        ).lower()
        assert "fibroblast" in text


def test_csv_export():
    state = make_fibrosis_fibroblast_state()
    csv_str = to_csv(state)
    assert "COL1A1" in csv_str or "disease-associated fibroblast" in csv_str
    lines = csv_str.strip().splitlines()
    assert len(lines) >= 2  # header + at least 1 row


def test_html_table_renders():
    state = make_fibrosis_fibroblast_state()
    html = render_evidence_table_html(state)
    assert "<table" in html
    assert 'data-sort-key="genes"' in html
    assert "maple-evidence-interactive" in html
    assert "fibroblast" in html.lower() or "col1a1" in html.lower()
