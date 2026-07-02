"""HTML UI components for the MAPLE Chainlit interface."""

from __future__ import annotations

import html as html_lib
import re

from models.schemas import ConfidenceLabel, FinalReport, PaperCoverage

_CONFIDENCE_CLASS: dict[str, str] = {
    "High": "maple-badge-high",
    "Medium": "maple-badge-medium",
    "Low": "maple-badge-low",
    "Insufficient": "maple-badge-insufficient",
}


def _e(text: object, fallback: str = "") -> str:
    value = str(text).strip() if text is not None else fallback
    return html_lib.escape(value or fallback)


def _confidence_class(label: str) -> str:
    return _CONFIDENCE_CLASS.get(label, "maple-badge-insufficient")


def _paragraphs(text: str) -> str:
    blocks = [p.strip() for p in re.split(r"\n{2,}", text or "") if p.strip()]
    if not blocks:
        return f'<p class="maple-muted">{_e("No content available.")}</p>'
    return "".join(f'<p class="maple-body-text">{_e(block)}</p>' for block in blocks)


def _list_items(items: list[str], empty_label: str = "None identified.") -> str:
    if not items:
        return f'<p class="maple-muted">{_e(empty_label)}</p>'
    rows = "".join(f"<li>{_e(item)}</li>" for item in items)
    return f'<ul class="maple-list">{rows}</ul>'


def welcome_hero() -> str:
    """Claude-inspired centered landing — input lives in the Chainlit composer below."""
    return """
<div class="maple-landing maple-animate">
  <div class="maple-landing-center">
    <div class="maple-logo-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" width="28" height="28" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M16 4c-1.2 4.8-4.8 7.2-9.6 8.4 4.8 1.2 7.2 4.8 8.4 9.6 1.2-4.8 4.8-7.2 9.6-8.4C20.8 11.2 17.2 8.8 16 4Z" fill="currentColor"/>
      </svg>
    </div>
    <h1 class="maple-landing-title">MAPLE</h1>
    <p class="maple-landing-expansion">Marker-based Annotation with PubMed Literature Evidence</p>
    <p class="maple-landing-subtitle">
      Enter marker genes and MAPLE finds which cell types the literature already
      annotates them to. The evidence table is the result.
    </p>
    <div id="maple-composer-anchor" class="maple-composer-anchor" aria-hidden="true"></div>
    <div class="maple-example-row">
      <span class="maple-example-label">Try an example:</span>
      <button type="button" class="maple-example-chip" data-genes="TP63, CDH2, CDKN1A, CDKN2A, KRT17, VIM">TP63, CDH2, CDKN1A, CDKN2A, KRT17, VIM</button>
      <button type="button" class="maple-example-chip" data-genes="COL1A1, COL3A1, POSTN, DCN">COL1A1, COL3A1, POSTN, DCN</button>
    </div>
    <p class="maple-credit">
      Built by <strong>Sai Rama Sridatta Prakki</strong> · Doctoral Candidate, Helmholtz Munich
    </p>
  </div>
</div>
""".strip()


def progress_panel_html(label: str, detail: str = "") -> str:
    detail_html = f'<div class="maple-progress-detail">{_e(detail)}</div>' if detail else ""
    return f"""
<div class="maple-progress maple-animate" role="status" aria-live="polite">
  <div class="maple-progress-spinner" aria-hidden="true"></div>
  <div class="maple-progress-copy">
    <div class="maple-progress-label">{_e(label)}</div>
    {detail_html}
  </div>
</div>
""".strip()


def status_banner(
    kind: str,
    title: str,
    body: str = "",
    *,
    icon: str = "",
) -> str:
    icon_html = f'<span class="maple-banner-icon">{_e(icon)}</span>' if icon else ""
    body_html = f'<p class="maple-banner-body">{_e(body)}</p>' if body else ""
    return f"""
<div class="maple-banner maple-banner-{kind} maple-animate">
  {icon_html}
  <div class="maple-banner-copy">
    <div class="maple-banner-title">{_e(title)}</div>
    {body_html}
  </div>
</div>
""".strip()


def search_banner(gene_count: int, context_str: str) -> str:
    return status_banner(
        "info",
        f"Searching PubMed for {gene_count} marker genes",
        context_str,
        icon="🔬",
    )


def result_hero(
    cell_type: str,
    confidence_label: ConfidenceLabel | str,
    confidence_score: float,
    umap_label: str,
) -> str:
    pct = max(0, min(100, int(round(confidence_score * 100))))
    badge_class = _confidence_class(str(confidence_label))
    return f"""
<div class="maple-result-hero maple-animate">
  <div class="maple-result-glow"></div>
  <div class="maple-result-content">
    <div class="maple-result-eyebrow">Final annotation</div>
    <h2 class="maple-result-type">{_e(cell_type)}</h2>
    <div class="maple-result-meta">
      <span class="maple-badge {badge_class}">{_e(confidence_label)} · {confidence_score:.2f}</span>
      <span class="maple-umap-chip">UMAP label · {_e(umap_label)}</span>
    </div>
    <div class="maple-score-track" aria-hidden="true">
      <div class="maple-score-fill {badge_class}" style="width:{pct}%"></div>
    </div>
  </div>
</div>
""".strip()


def section_card(title: str, body: str, *, icon: str = "") -> str:
    icon_html = f'<span class="maple-section-icon">{_e(icon)}</span>' if icon else ""
    return f"""
<section class="maple-card maple-section-card">
  <header class="maple-section-head">
    {icon_html}
    <h3 class="maple-section-title">{_e(title)}</h3>
  </header>
  <div class="maple-section-body">{_paragraphs(body)}</div>
</section>
""".strip()


def chip_list(title: str, items: list[str], *, icon: str = "") -> str:
    if not items:
        chips = f'<span class="maple-chip maple-chip-muted">{_e("None")}</span>'
    else:
        chips = "".join(f'<span class="maple-chip">{_e(item)}</span>' for item in items)
    icon_html = f'<span class="maple-section-icon">{_e(icon)}</span>' if icon else ""
    return f"""
<section class="maple-card maple-section-card">
  <header class="maple-section-head">
    {icon_html}
    <h3 class="maple-section-title">{_e(title)}</h3>
  </header>
  <div class="maple-chip-row">{chips}</div>
</section>
""".strip()


def list_card(title: str, items: list[str], *, icon: str = "", empty_label: str = "None identified.") -> str:
    icon_html = f'<span class="maple-section-icon">{_e(icon)}</span>' if icon else ""
    return f"""
<section class="maple-card maple-section-card">
  <header class="maple-section-head">
    {icon_html}
    <h3 class="maple-section-title">{_e(title)}</h3>
  </header>
  <div class="maple-section-body">{_list_items(items, empty_label)}</div>
</section>
""".strip()


def literature_table_intro(total_rows: int, marker_count: int) -> str:
    return f"""
<div class="maple-table-intro maple-animate">
  <div class="maple-table-intro-icon">📚</div>
  <div>
    <h3 class="maple-table-intro-title">Literature evidence</h3>
    <p class="maple-table-intro-copy">
      <strong>{total_rows}</strong> paper × cell-type rows across <strong>{marker_count}</strong> input genes.
      Click column headers to sort · use pagination controls below the table.
    </p>
  </div>
</div>
""".strip()


def literature_table_html(ranked: list[PaperCoverage]) -> str:
    """Interactive sortable/paginated table (DataTables init in custom.js)."""
    rows: list[str] = []
    for i, paper in enumerate(ranked, start=1):
        if paper.cell_type_specific_genes:
            attr_genes = ", ".join(paper.cell_type_specific_genes)
            attr_count = len(paper.cell_type_specific_genes)
        else:
            attr_genes = ", ".join(paper.matched_markers)
            attr_count = paper.marker_count
        count_display = f"{attr_count}/{paper.marker_count}"
        source = "full text" if paper.full_text_used else "abstract"
        year = paper.year if paper.year is not None else ""
        pmid_cell = (
            f'<a href="{_e(paper.pubmed_url)}" target="_blank" rel="noopener">{_e(paper.pmid)}</a>'
            if paper.pubmed_url.startswith("http")
            else _e(paper.pmid)
        )
        rows.append(
            "<tr>"
            f'<td data-order="{i}">{i}</td>'
            f"<td>{_e(paper.cell_type or '—')}</td>"
            f"<td>{_e(attr_genes)}</td>"
            f'<td data-order="{attr_count}">{_e(count_display)}</td>'
            f"<td>{_e(source)}</td>"
            f'<td data-order="{year if year else 0}">{_e(year or "—")}</td>'
            f"<td>{pmid_cell}</td>"
            f"<td>{_e(paper.title)}</td>"
            "</tr>"
        )

    body = "\n".join(rows) if rows else (
        '<tr><td colspan="8" class="maple-muted">No rows to display.</td></tr>'
    )
    return f"""
<div class="maple-table-shell maple-wide-block">
  <table id="maple-literature-table" class="maple-datatable display" style="width:100%">
    <thead>
      <tr>
        <th>#</th>
        <th>Cell type</th>
        <th>Attributed genes</th>
        <th>Attr/Found</th>
        <th>Source</th>
        <th>Year</th>
        <th>PMID</th>
        <th>Title</th>
      </tr>
    </thead>
    <tbody>
      {body}
    </tbody>
  </table>
</div>
""".strip()


def render_report_html(report: FinalReport) -> str:
    """Build the styled HTML dashboard for a completed annotation."""
    c = report.consensus
    parts = [
        result_hero(c.final_cell_type, c.confidence_label, c.confidence_score, c.umap_label),
        '<div class="maple-grid-2">',
        section_card("Biological interpretation", c.biological_interpretation or "No interpretation available.", icon="🧬"),
        section_card("Executive summary", c.concise_summary or "No summary available.", icon="📋"),
        "</div>",
        '<div class="maple-grid-2">',
        section_card("Marker-rule inference", c.marker_based_inference or "N/A", icon="🎯"),
        section_card("PubMed evidence", c.pubmed_evidence_summary or "No evidence.", icon="📄"),
        "</div>",
        section_card("Reviewer caveats", c.reviewer_caveats_summary or "No major caveats.", icon="🔍"),
        '<div class="maple-grid-2">',
        list_card("Alternative annotations", c.alternative_annotations, icon="↔️"),
        list_card("Reviewer flags", report.reviewer_result.key_caveats, icon="⚠️"),
        "</div>",
        chip_list("Recommended next markers", c.recommended_next_markers, icon="➕"),
    ]
    return "\n".join(parts)
