"""Render the evidence table for MAPLE."""
from __future__ import annotations

import csv
import html as _html
import io

from maple.models import AnalysisState, EvidenceRow, TableState

# Mapping from user-facing sort term → EvidenceRow attribute name
SORT_KEYS: dict[str, str] = {
    "number_of_user_genes_found": "number_of_user_genes_found",
    "genes": "number_of_user_genes_found",
    "date": "publication_year",
    "year": "publication_year",
    "strength": "match_strength",
    "evidence_type": "evidence_type",
    "type": "evidence_type",
    "celltype": "celltype_label",
    "journal": "journal",
    "title": "paper_title",
}

STRENGTH_ORDER: dict[str, int] = {"High": 3, "Medium": 2, "Low": 1}

EVIDENCE_TYPE_ORDER: dict[str, int] = {
    "direct_marker_celltype_assignment": 5,
    "cluster_annotation": 4,
    "differential_expression_only": 2,
    "gene_mention_only": 1,
    "celltype_mention_only": 0,
    "unrelated": 0,
}

# Column header → row data-attribute used for client-side sorting
COLUMN_SORT_KEYS: list[tuple[str, str]] = [
    ("# Genes", "genes"),
    ("Genes in Evidence", "genes_text"),
    ("Cell Type (from Paper)", "celltype"),
    ("Evidence Type", "evidence_type"),
    ("Paper Title", "title"),
    ("Year", "year"),
    ("Strength", "strength"),
    ("Tissue", "tissue"),
    ("Disease", "disease"),
    ("Species", "species"),
    ("Source Passage", "evidence"),
]


def _e(text: object) -> str:
    if text is None:
        return ""
    return _html.escape(str(text))


def _attr(text: object) -> str:
    """Escape for HTML attribute values."""
    if text is None:
        return ""
    return _html.escape(str(text), quote=True)


# ─── Data helpers ─────────────────────────────────────────────────────────────

def get_filtered_rows(state: AnalysisState) -> list[EvidenceRow]:
    """Apply search filter from state.table_state.search_query."""
    rows: list[EvidenceRow] = []
    if state.extraction:
        rows = list(state.extraction.evidence_rows)

    query = (state.table_state.search_query or "").strip().lower()
    if not query:
        return rows

    filtered: list[EvidenceRow] = []
    for row in rows:
        haystack = " ".join(
            filter(None, [
                row.celltype_label or "",
                row.paper_title or "",
                " ".join(row.matched_user_genes),
                row.tissue or "",
                row.disease or "",
                row.species or "",
                row.evidence_snippet or "",
                row.evidence_type or "",
                row.journal or "",
            ])
        ).lower()
        if query in haystack:
            filtered.append(row)
    return filtered


def get_sorted_rows(rows: list[EvidenceRow], table_state: TableState) -> list[EvidenceRow]:
    """Sort rows per table_state.sort_by and sort_direction."""
    sort_attr = SORT_KEYS.get(table_state.sort_by, table_state.sort_by)
    reverse = table_state.sort_direction.lower() == "desc"

    def sort_key(row: EvidenceRow):
        val = getattr(row, sort_attr, None)
        if sort_attr == "match_strength":
            return STRENGTH_ORDER.get(str(val), 0)
        if val is None:
            # Put Nones last regardless of direction
            return -1 if reverse else 9_999_999
        return val

    return sorted(rows, key=sort_key, reverse=reverse)


def get_page(
    rows: list[EvidenceRow],
    table_state: TableState,
) -> tuple[list[EvidenceRow], int, int]:
    """Return (page_rows, total_pages, total_rows)."""
    total = len(rows)
    page_size = max(1, table_state.page_size)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(table_state.page, total_pages))
    start = (page - 1) * page_size
    end = start + page_size
    return rows[start:end], total_pages, total


# ─── Badge helper ─────────────────────────────────────────────────────────────

def _strength_badge(strength: str) -> str:
    cls_map = {
        "High":   "maple-badge maple-badge-high",
        "Medium": "maple-badge maple-badge-medium",
        "Low":    "maple-badge maple-badge-low",
    }
    cls = cls_map.get(strength, "maple-badge maple-badge-low")
    return f'<span class="{cls}">{_e(strength)}</span>'


# ─── HTML renderer ────────────────────────────────────────────────────────────

def _evidence_type_badge(evidence_type: str) -> str:
    labels = {
        "direct_marker_celltype_assignment": "Direct assignment",
        "cluster_annotation": "Cluster annotation",
        "differential_expression_only": "DE only",
        "gene_mention_only": "Gene mention",
        "celltype_mention_only": "Cell type mention",
        "unrelated": "Unrelated",
    }
    label = labels.get(evidence_type, evidence_type.replace("_", " "))
    return f'<span class="maple-badge maple-badge-medium" title="{_e(evidence_type)}">{_e(label)}</span>'


def _row_tr(row: EvidenceRow) -> str:
    """Single evidence row with data-* attributes for client-side sort/filter."""
    genes_str = ", ".join(row.matched_user_genes) if row.matched_user_genes else "—"
    title_raw = row.paper_title or ""
    title_short = (title_raw[:80] + "…") if len(title_raw) > 80 else title_raw
    pmid_link = (
        f'<a href="https://pubmed.ncbi.nlm.nih.gov/{_e(row.pmid)}/" '
        f'target="_blank" rel="noopener">{_e(row.pmid)}</a>'
    )
    snippet_raw = row.evidence_snippet or ""
    snippet = (snippet_raw[:260] + "…") if len(snippet_raw) > 260 else snippet_raw
    strength_val = STRENGTH_ORDER.get(row.match_strength, 0)
    type_val = EVIDENCE_TYPE_ORDER.get(row.evidence_type, 0)
    year_val = row.publication_year if row.publication_year is not None else 0

    return f"""<tr
  data-genes="{row.number_of_user_genes_found}"
  data-genes-text="{_attr(genes_str.lower())}"
  data-celltype="{_attr((row.celltype_label or '').lower())}"
  data-evidence-type="{type_val}"
  data-title="{_attr(title_raw.lower())}"
  data-year="{year_val}"
  data-strength="{strength_val}"
  data-tissue="{_attr((row.tissue or '').lower())}"
  data-disease="{_attr((row.disease or '').lower())}"
  data-species="{_attr((row.species or '').lower())}"
  data-evidence="{_attr(snippet_raw.lower())}"
>
  <td style="text-align:center;font-weight:700;">{_e(row.number_of_user_genes_found)}</td>
  <td><code style="font-size:11px;word-break:break-all;">{_e(genes_str)}</code></td>
  <td><strong>{_e(row.celltype_label)}</strong></td>
  <td>{_evidence_type_badge(row.evidence_type)}</td>
  <td style="font-size:12px;">{_e(title_short)} [{pmid_link}]</td>
  <td style="text-align:center;">{_e(row.publication_year) if row.publication_year else "—"}</td>
  <td>{_strength_badge(row.match_strength)}</td>
  <td>{_e(row.tissue) if row.tissue else "—"}</td>
  <td>{_e(row.disease) if row.disease else "—"}</td>
  <td>{_e(row.species) if row.species else "—"}</td>
  <td class="maple-source-passage">{_e(snippet)}</td>
</tr>"""


def render_evidence_table_html(state: AnalysisState) -> str:
    """Return HTML for the evidence table (all rows; sort/page handled in browser)."""
    rows = get_filtered_rows(state)
    ts = state.table_state

    if not rows:
        empty_msg = (
            "No rows match the current filter."
            if ts.search_query
            else "No evidence rows extracted."
        )
        return (
            f'<div class="maple-evidence-table-container">'
            f'<p class="maple-muted">{_e(empty_msg)}</p>'
            f"</div>"
        )

    tbody = "\n".join(_row_tr(row) for row in rows)

    thead_cells = []
    for label, sort_key in COLUMN_SORT_KEYS:
        sort_cls = ' class="maple-sort-desc"' if sort_key == "genes" else ""
        thead_cells.append(
            f'<th data-sort-key="{sort_key}"{sort_cls} scope="col">{_e(label)}</th>'
        )
    thead = "\n          ".join(thead_cells)

    colgroup = (
        '<colgroup>'
        '<col style="width:3%">'    # # Genes
        '<col style="width:9%">'    # Genes in Evidence
        '<col style="width:14%">'   # Cell Type
        '<col style="width:11%">'   # Evidence Type
        '<col style="width:18%">'   # Paper Title
        '<col style="width:4%">'    # Year
        '<col style="width:6%">'    # Strength
        '<col style="width:7%">'    # Tissue
        '<col style="width:7%">'    # Disease
        '<col style="width:5%">'    # Species
        '<col style="width:16%">'   # Source Passage
        '</colgroup>'
    )

    return f"""<div class="maple-evidence-table-container maple-animate maple-evidence-interactive"
     data-page-size="{ts.page_size}"
     data-default-sort="genes"
     data-default-direction="desc">
  <h3 style="margin:0 0 8px;color:var(--maple-text);">Evidence Table</h3>
  <div class="maple-table-toolbar">
    <input type="search" class="maple-table-filter" placeholder="Filter rows…" aria-label="Filter evidence rows">
    <label class="maple-table-toggle" title="Show only cell types supported by 2 or more of your markers">
      <input type="checkbox" class="maple-table-multigene" checked> ≥2 genes only
    </label>
    <button type="button" class="maple-table-csv" aria-label="Download full evidence table as CSV">
      <span aria-hidden="true">⬇</span> Download CSV
    </button>
  </div>
  <div class="maple-table-scroll">
    <table class="maple-evidence-table" style="width:100%;">
      {colgroup}
      <thead>
        <tr>
          {thead}
        </tr>
      </thead>
      <tbody>
        {tbody}
      </tbody>
    </table>
  </div>
  <div class="maple-table-nav">
    <label class="maple-table-pagesize-label">Rows per page:
      <select class="maple-table-pagesize" aria-label="Rows per page">
        <option value="10" selected>10</option>
        <option value="25">25</option>
        <option value="50">50</option>
        <option value="100">100</option>
        <option value="-1">All</option>
      </select>
    </label>
    <button type="button" class="maple-table-prev" disabled>Prev</button>
    <span class="maple-table-page-info">Page <strong class="maple-table-page">1</strong> of <strong class="maple-table-pages">1</strong></span>
    <button type="button" class="maple-table-next" disabled>Next</button>
    <span class="maple-table-total">· <strong class="maple-table-count">{len(rows)}</strong> rows</span>
    <span class="maple-table-hint">Click column headers to sort</span>
  </div>
</div>"""


# ─── CSV export ───────────────────────────────────────────────────────────────

def to_csv(state: AnalysisState) -> str:
    """Return CSV string of all evidence rows (no filtering, no paging)."""
    rows: list[EvidenceRow] = []
    if state.extraction:
        rows = state.extraction.evidence_rows

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "# Genes Found",
        "Genes in Paper",
        "Cell Type",
        "Paper Title",
        "PMID",
        "Journal",
        "Year",
        "Strength",
        "Evidence Type",
        "Tissue",
        "Disease",
        "Species",
        "Source Passage",
        "Source URL",
    ])
    for row in rows:
        writer.writerow([
            row.number_of_user_genes_found,
            ", ".join(row.matched_user_genes),
            row.celltype_label,
            row.paper_title,
            row.pmid,
            row.journal or "",
            row.publication_year or "",
            row.match_strength,
            row.evidence_type,
            row.tissue or "",
            row.disease or "",
            row.species or "",
            row.evidence_snippet,
            row.source_url or "",
        ])
    return output.getvalue()
