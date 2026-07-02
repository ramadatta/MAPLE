"""Render the MAPLE Reasoning Trace as HTML."""
from __future__ import annotations

import html as _html

from maple.models import AnalysisState


def _e(text: object) -> str:
    """HTML-escape a value, converting to string first."""
    if text is None:
        return ""
    return _html.escape(str(text))


def render_audit_trail_html(state: AnalysisState) -> str:
    """Return HTML for the full reasoning trace."""

    # --- Audit trail entries ---
    entries_html_parts: list[str] = []
    for entry in state.audit_trail:
        detail_html = (
            f'<div class="maple-audit-detail">{_e(entry.detail)}</div>'
            if entry.detail
            else ""
        )
        entries_html_parts.append(
            f"""<div class="maple-audit-entry">
  <span class="maple-agent-chip">{_e(entry.agent)}</span>
  <div class="maple-audit-entry-body">
    <span class="maple-audit-message">{_e(entry.message)}</span>
    {detail_html}
  </div>
</div>"""
        )
    entries_html = "\n".join(entries_html_parts) if entries_html_parts else (
        '<div class="maple-audit-entry maple-muted">No audit entries recorded.</div>'
    )

    # --- Statistics rows ---
    stats_parts: list[str] = []
    if state.retrieval:
        r = state.retrieval
        stats_parts.append(
            f"Papers searched: <strong>{r.total_searched}</strong> | "
            f"Deduplicated: <strong>{r.total_after_dedup}</strong> | "
            f"PMC full-text: <strong>{r.fulltext_count}</strong>"
        )
    if state.extraction:
        e = state.extraction
        stats_parts.append(
            f"Evidence rows found: <strong>{len(e.evidence_rows)}</strong> | "
            f"Papers excluded: <strong>{e.excluded_paper_count}</strong>"
        )
        stats_parts.append(
            f"Abstract-only papers: <strong>{e.abstract_only_papers}</strong> | "
            f"Direct assignment papers: <strong>{e.papers_with_direct_assignment}</strong> | "
            f"Cluster annotation papers: <strong>{e.papers_with_cluster_annotation}</strong>"
        )
        stats_parts.append(
            f"DE-only papers: <strong>{e.papers_de_only}</strong> | "
            f"Gene-mention-only papers: <strong>{e.papers_gene_mention_only}</strong> | "
            f"No celltype assignment: <strong>{e.papers_no_celltype_assignment}</strong>"
        )
    if state.candidates:
        labels = [
            f"{_e(c.candidate_label)} ({c.candidate_score:.2f})"
            for c in state.candidates.candidate_labels[:5]
        ]
        cand_str = ", ".join(labels) if labels else "none"
        stats_parts.append(f"Candidates: <strong>{cand_str}</strong>")
    if state.devils_advocate:
        da = state.devils_advocate
        adj = _e(da.recommended_confidence_adjustment)
        ca = _e(da.strongest_counterargument[:120]) if da.strongest_counterargument else "—"
        stats_parts.append(
            f"Devil's Advocate: {ca} | Recommended: <strong>{adj}</strong>"
        )
    if state.consensus:
        c = state.consensus
        stats_parts.append(
            f"Consensus: <strong>{_e(c.consensus_label)}</strong> | "
            f"Confidence: <strong>{_e(c.confidence)}</strong>"
        )

    stats_html = "".join(
        f'<div class="maple-audit-stat">{s}</div>' for s in stats_parts
    )

    # --- Errors ---
    errors_html = ""
    if state.errors:
        error_items = "".join(f"<li>{_e(err)}</li>" for err in state.errors)
        errors_html = (
            f'<div class="maple-audit-errors">'
            f"<strong>Errors:</strong><ul>{error_items}</ul>"
            f"</div>"
        )

    return f"""<div class="maple-audit-trail maple-animate">
  <div class="maple-audit-header">
    <span class="maple-audit-title">MAPLE Reasoning Trace</span>
  </div>
  <hr class="maple-audit-rule">
  <div class="maple-audit-entries">
    {entries_html}
  </div>
  {f'<div class="maple-audit-stats">{stats_html}</div>' if stats_html else ""}
  {errors_html}
</div>"""
