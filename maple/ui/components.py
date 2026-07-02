"""HTML components for the MAPLE UI."""
from __future__ import annotations

import html as _html

from maple.models import AnalysisState, ConsensusResult, DevilsAdvocateResult

_DISCLAIMER = (
    "⚠️ Research use only. Not a clinical or diagnostic tool. "
    "Expert review of cited papers is required before publication, "
    "diagnosis, or therapeutic decisions."
)

_CONFIDENCE_STYLES: dict[str, tuple[str, str, str]] = {
    # (css-class, text-color, background-color)
    "High":         ("maple-confidence-high",         "#2f7a4f", "rgba(47,122,79,0.12)"),
    "Medium":       ("maple-confidence-medium",       "#8a6914", "rgba(184,134,11,0.12)"),
    "Low":          ("maple-confidence-low",          "#a04e32", "rgba(201,100,66,0.12)"),
    "Insufficient": ("maple-confidence-insufficient", "#9a3a3a", "rgba(184,74,74,0.10)"),
}


def _e(text: object) -> str:
    if text is None:
        return ""
    return _html.escape(str(text))


def _chip_row(items: list[str], empty: str = "None identified.") -> str:
    if not items:
        return f'<em class="maple-muted">{_e(empty)}</em>'
    chips = "".join(f'<span class="maple-chip">{_e(g)}</span>' for g in items)
    return f'<div class="maple-chip-row">{chips}</div>'


def _list_html(items: list[str], empty: str = "None identified.") -> str:
    if not items:
        return f'<p class="maple-muted">{_e(empty)}</p>'
    rows = "".join(f"<li>{_e(it)}</li>" for it in items)
    return f'<ul class="maple-list">{rows}</ul>'


# ─── Consensus Panel ──────────────────────────────────────────────────────────

def render_consensus_panel_html(state: AnalysisState) -> str:
    """Return HTML for the consensus cell-type panel."""
    c: ConsensusResult | None = state.consensus
    if not c:
        return '<div class="maple-consensus-panel"><p class="maple-muted">No consensus result available.</p></div>'

    confidence = c.confidence or "Insufficient"
    css_cls, txt_color, bg_color = _CONFIDENCE_STYLES.get(
        confidence, _CONFIDENCE_STYLES["Insufficient"]
    )
    badge = (
        f'<span class="maple-badge {css_cls}" '
        f'style="background:{bg_color};color:{txt_color};border:1px solid {txt_color}44;">'
        f"{_e(confidence)}</span>"
    )

    # Supporting papers bullets
    papers_parts: list[str] = []
    for pmid in c.main_supporting_pmids:
        url = f"https://pubmed.ncbi.nlm.nih.gov/{_e(pmid)}/"
        papers_parts.append(
            f'<li><a href="{url}" target="_blank" rel="noopener">PMID {_e(pmid)}</a></li>'
        )
    if not papers_parts:
        papers_parts.append("<li><em>No papers cited.</em></li>")
    papers_html = f'<ul class="maple-list">{"".join(papers_parts)}</ul>'

    # Alternative labels
    alts_html = ""
    if c.alternative_labels:
        alt_items = "".join(
            f"<li><strong>{_e(a.label)}</strong> — {_e(a.reason_not_selected)}</li>"
            for a in c.alternative_labels
        )
        alts_html = f"""<div class="maple-consensus-section">
  <h4>Alternative Interpretations</h4>
  <ul class="maple-list">{alt_items}</ul>
</div>"""

    # What would improve confidence
    improve_html = ""
    if c.what_would_improve_confidence:
        improve_html = f"""<div class="maple-consensus-section">
  <h4>What Would Improve Confidence</h4>
  {_list_html(c.what_would_improve_confidence)}
</div>"""

    return f"""<div class="maple-consensus-panel maple-animate">
  <div class="maple-consensus-header">
    <h2 class="maple-consensus-label">{_e(c.consensus_label)}</h2>
    {badge}
  </div>
  <div class="maple-consensus-body">
    <p class="maple-body-text">{_e(c.consensus_rationale) or "<em>No rationale provided.</em>"}</p>
    <div class="maple-consensus-section">
      <h4>Supporting Genes</h4>
      {_chip_row(c.supporting_genes)}
    </div>
    <div class="maple-consensus-section">
      <h4>Key Supporting Papers</h4>
      {papers_html}
    </div>
    {alts_html}
    {improve_html}
  </div>
  <div class="maple-disclaimer">{_DISCLAIMER}</div>
</div>"""


# ─── Devil's Advocate Panel ───────────────────────────────────────────────────

def render_devils_advocate_html(state: AnalysisState) -> str:
    """Return HTML for the Devil's Advocate critique panel."""
    da: DevilsAdvocateResult | None = state.devils_advocate
    if not da:
        return (
            '<div class="maple-da-panel">'
            '<p class="maple-muted">No Devil\'s Advocate result available.</p>'
            '</div>'
        )

    adj = da.recommended_confidence_adjustment or "keep"
    adj_colors = {"raise": "#2f7a4f", "keep": "#8a6914", "lower": "#a04e32"}
    adj_color = adj_colors.get(adj, "#6b6a65")

    # Alternative labels
    alt_items: list[str] = []
    for a in da.possible_alternative_labels:
        genes_str = (
            f" (genes: {', '.join(_e(g) for g in a.supporting_genes)})"
            if a.supporting_genes
            else ""
        )
        alt_items.append(
            f"<li><strong>{_e(a.label)}</strong> — {_e(a.reason)}{genes_str}</li>"
        )
    alts_html = (
        f'<ul class="maple-list">{"".join(alt_items)}</ul>'
        if alt_items
        else '<p class="maple-muted">None identified.</p>'
    )

    return f"""<div class="maple-da-panel maple-animate">
  <div class="maple-da-header">
    <h3 class="maple-da-title">Devil's Advocate Review</h3>
    <span class="maple-da-adj" style="color:{adj_color};font-weight:600;">
      Recommend: {_e(adj.title())}
    </span>
  </div>
  <div class="maple-da-body">
    <div class="maple-da-section">
      <h4>Strongest Counterargument</h4>
      <p class="maple-body-text">{_e(da.strongest_counterargument) or "<em>None.</em>"}</p>
    </div>
    <div class="maple-da-section">
      <h4>Nonspecific Genes</h4>
      {_chip_row(da.nonspecific_genes, "None identified.")}
    </div>
    <div class="maple-da-section">
      <h4>Possible Alternative Labels</h4>
      {alts_html}
    </div>
    <div class="maple-da-section">
      <h4>Confidence Adjustment Reason</h4>
      <p class="maple-body-text">{_e(da.confidence_adjustment_reason) or "<em>N/A</em>"}</p>
    </div>
    <div class="maple-da-section">
      <h4>Additional Markers Needed</h4>
      {_list_html(da.additional_markers_needed, "None specified.")}
    </div>
  </div>
</div>"""
