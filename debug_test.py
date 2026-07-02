#!/usr/bin/env python
"""Quick debug test to run the pipeline with logging output."""

import logging
import sys
from pathlib import Path

# Set up logging to see debug output
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
from models.schemas import PubMedPaper, UserInput
from services.pubmed_service import EUtilitiesBackend

# Sample input
user_input = UserInput(
    genes=["SFTPC", "SFTPB"],
    tissue="Human lung",
    disease="Idiopathic Pulmonary Fibrosis",
    species="Human",
)

print("\n" + "="*60)
print("MAPLE DEBUG TEST")
print("="*60)
print(f"Input: {user_input.genes}")
print(f"Tissue: {user_input.tissue}")
print(f"Disease: {user_input.disease}\n")

# Run marker agent
print("1. Running Marker Agent...")
marker_result = run_marker_agent(user_input, llm=None)
print(f"   Top candidate: {marker_result.candidates[0].cell_type}\n")

# Run query builder
print("2. Running Query Builder...")
queries = build_queries(user_input, marker_result)
print(f"   Generated {len(queries)} queries\n")

# Retrieve papers
print("3. Running PubMed Service...")
pubmed = EUtilitiesBackend()
papers_by_gene = {}
seen_genes = set()
for query in queries[:5]:  # Limit to 5 queries for speed
    if query.gene in seen_genes:
        continue
    seen_genes.add(query.gene)
    pmids = pubmed.search(query.query, max_results=5)
    if pmids:
        papers = pubmed.fetch_papers(pmids)
        papers_by_gene[query.gene] = papers
        print(f"   {query.gene}: found {len(papers)} papers")
    else:
        print(f"   {query.gene}: no results")

# Run evidence agent
print("\n4. Running Evidence Agent...")
evidence_result = run_evidence_agent(user_input, papers_by_gene, marker_result, llm=None)
print(f"   Extracted {len(evidence_result.evidence_items)} evidence items\n")

# Run reviewer
print("5. Running Reviewer Agent...")
reviewer_result = run_deterministic_reviewer_checks(user_input, marker_result, evidence_result)
print(f"   {len(reviewer_result.key_caveats)} caveats identified\n")

# Run consensus
print("6. Running Consensus Agent...")
report = run_consensus_agent(
    user_input,
    marker_result,
    evidence_result,
    reviewer_result,
    llm=None,
)
print(f"   Final cell type: {report.consensus.final_cell_type}")
print(f"   Confidence: {report.consensus.confidence_label} ({report.consensus.confidence_score:.2f})\n")

print("="*60)
print("✓ DEBUG TEST COMPLETE")
print("="*60)
print("\nIf you see debug logs above, logging is working.")
print("Look for '[app]' lines showing markdown generation details.\n")
