#!/usr/bin/env python
"""Debug the render_report function to see what markdown is generated."""

import logging
import sys
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

# Add project root to path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.consensus_agent import run_consensus_agent
from agents.evidence_agent import run_evidence_agent
from agents.marker_agent import run_marker_agent
from agents.query_builder import build_queries
from agents.reviewer_agent import run_deterministic_reviewer_checks
from models.schemas import UserInput
from services.pubmed_service import EUtilitiesBackend
from utils.markdown_safe import sanitize_markdown_for_chainlit, markdown_cell

# Sample input
user_input = UserInput(
    genes=["SFTPC", "SFTPB"],
    tissue="Human lung",
    disease="Idiopathic Pulmonary Fibrosis",
    species="Human",
)

print("\n" + "="*60)
print("MAPLE RENDER DEBUG TEST")
print("="*60 + "\n")

# Run pipeline
marker_result = run_marker_agent(user_input, llm=None)
queries = build_queries(user_input, marker_result)

pubmed = EUtilitiesBackend()
papers_by_gene = {}
seen_genes = set()
for query in queries[:5]:
    if query.gene in seen_genes:
        continue
    seen_genes.add(query.gene)
    pmids = pubmed.search(query.query, max_results=5)
    if pmids:
        papers = pubmed.fetch_papers(pmids)
        papers_by_gene[query.gene] = papers

evidence_result = run_evidence_agent(user_input, papers_by_gene, marker_result, llm=None)
reviewer_result = run_deterministic_reviewer_checks(user_input, marker_result, evidence_result)
report = run_consensus_agent(user_input, marker_result, evidence_result, reviewer_result, llm=None)

# Now test the report rendering
c = report.consensus

print("Testing report content generation:\n")

# Test 1: Consensus table
msg1 = f"""## 1. Final Consensus Report

| Field | Value |
|-------|-------|
| **Final cell type** | {markdown_cell(c.final_cell_type, "Unknown")} |
| **Confidence score** | {c.confidence_score:.2f} |
| **Confidence label** | {markdown_cell(c.confidence_label)} |
| **UMAP-ready label** | `{markdown_cell(c.umap_label, "Unknown")}` |

**Interpretation:** {markdown_cell(c.biological_interpretation, "No interpretation available.")}

**Summary:** {markdown_cell(c.concise_summary)}

---
**Marker-based inference:** {markdown_cell(c.marker_based_inference, "N/A")}

**PubMed evidence:** {markdown_cell(c.pubmed_evidence_summary, "N/A")}

**Reviewer caveats:** {markdown_cell(c.reviewer_caveats_summary, "No major caveats.")}
"""
print("✓ Message 1 (consensus) created")
print(f"  Length: {len(msg1)} chars\n")

# Test 2: Evidence table
df = report.evidence_dataframe()
display_df = df[
    [
        "Gene",
        "Predicted Cell Type",
        "Confidence",
        "Evidence Type",
        "PMID",
        "Paper Title",
        "Year",
        "Tissue Match",
        "Disease Match",
    ]
] if not df.empty else df

from app import _evidence_table_markdown
msg2 = "## 2. Evidence Table\n\n" + _evidence_table_markdown(display_df)
print("✓ Message 2 (evidence table) created")
print(f"  Length: {len(msg2)} chars\n")

# Test 3: Papers
from app import _pubmed_link
print("Testing paper links:\n")
for item in report.evidence_result.evidence_items[:3]:
    pmid_link = _pubmed_link(item.pmid, item.pubmed_url)
    print(f"  PMID {item.pmid}:")
    print(f"    URL: {repr(item.pubmed_url)}")
    print(f"    Link: {repr(pmid_link)}")

    paper_content = sanitize_markdown_for_chainlit(
        f"**{markdown_cell(item.paper_title, 'Untitled paper')}**\n\n"
        f"- **PMID:** {pmid_link}\n"
        f"- **Year:** {markdown_cell(item.year, 'N/A')}\n"
        f"- **Journal:** {markdown_cell(item.journal, 'N/A')}\n"
        f"- **Evidence:** {markdown_cell(item.evidence_sentence)[:300]}\n"
    )
    print(f"    Content length: {len(paper_content)} chars\n")

print("="*60)
print("✓ RENDER DEBUG COMPLETE - No errors detected")
print("="*60)
print("\nIf you see this, all markdown generation is working.")
print("The null error must be happening in Chainlit's rendering.\n")
