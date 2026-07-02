#!/usr/bin/env python3
"""
MAPLE evaluation runner.

Run with:
    python -m scripts.run_evals
    python -m scripts.run_evals --case fibroblast
    python -m scripts.run_evals --case all --llm  # live LLM (requires API key)
"""
import argparse
import asyncio
import os
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maple.models import AnalysisInput
from maple.runtime.orchestrator import run_maple_pipeline

EVAL_CASES = {
    "fibroblast": AnalysisInput(
        markers=["COL1A1", "COL3A1", "POSTN", "CTHRC1", "DCN", "LUM"],
        tissue="lung",
        disease="idiopathic pulmonary fibrosis",
        species="human",
    ),
    "at2": AnalysisInput(
        markers=["SFTPC", "SFTPA1", "SFTPA2", "ABCA3"],
        tissue="lung",
        species="human",
    ),
    "tcell": AnalysisInput(
        markers=["CD3D", "CD3E", "TRAC", "IL7R"],
        tissue="blood",
        disease="healthy",
        species="human",
    ),
    "macrophage": AnalysisInput(
        markers=["LYZ", "C1QA", "C1QB", "C1QC", "CD68"],
        tissue="lung",
        disease="fibrosis",
        species="human",
    ),
    "proliferative": AnalysisInput(
        markers=["MKI67", "TOP2A", "EPCAM", "KRT8"],
        tissue="tumor",
        disease="cancer",
        species="human",
    ),
    "weak": AnalysisInput(
        markers=["ACTB", "GAPDH", "MALAT1"],
    ),
}


async def run_case(name: str, inp: AnalysisInput, llm=None) -> dict:
    print(f"\n{'='*60}")
    print(f"Case: {name}")
    print(f"Markers: {', '.join(inp.markers)}")
    if inp.tissue:
        print(f"Tissue: {inp.tissue}")
    if inp.disease:
        print(f"Disease: {inp.disease}")
    print("Running pipeline...")

    state = await run_maple_pipeline(inp, llm=llm)

    if state.consensus:
        print(f"\nResult:")
        print(f"  Label: {state.consensus.consensus_label}")
        print(f"  Confidence: {state.consensus.confidence}")
        if state.consensus.consensus_rationale:
            print(f"  Rationale: {state.consensus.consensus_rationale[:200]}...")
        if state.consensus.supporting_genes:
            print(f"  Supporting genes: {', '.join(state.consensus.supporting_genes[:5])}")
        if state.consensus.main_supporting_pmids:
            print(f"  PMIDs: {', '.join(state.consensus.main_supporting_pmids[:3])}")

    if state.extraction:
        print(f"\n  Evidence rows: {len(state.extraction.evidence_rows)}")
        print(f"  Excluded papers: {state.extraction.excluded_paper_count}")

    if state.errors:
        print(f"\n  ERRORS: {state.errors}")

    return {
        "case": name,
        "consensus_label": state.consensus.consensus_label if state.consensus else "Error",
        "confidence": state.consensus.confidence if state.consensus else "Error",
        "evidence_row_count": len(state.extraction.evidence_rows) if state.extraction else 0,
        "errors": state.errors,
    }


async def main():
    parser = argparse.ArgumentParser(description="MAPLE evaluation runner")
    parser.add_argument(
        "--case", default="all", choices=list(EVAL_CASES.keys()) + ["all"]
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Use live LLM (requires OPENAI_API_KEY or equivalent)",
    )
    parser.add_argument("--output", default=None, help="Save JSON results to file")
    args = parser.parse_args()

    llm = None
    if args.llm:
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GITLAB_TOKEN")
        if not api_key:
            print("ERROR: --llm requires OPENAI_API_KEY or GITLAB_TOKEN in environment")
            sys.exit(1)
        from services.llm_service import create_llm_service
        llm = create_llm_service(api_key)
        print("LLM mode enabled")
    else:
        print("Heuristic mode (no LLM). Use --llm for LLM-backed evaluation.")

    cases = {args.case: EVAL_CASES[args.case]} if args.case != "all" else EVAL_CASES
    results = []

    for name, inp in cases.items():
        result = await run_case(name, inp, llm)
        results.append(result)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        status = "OK" if not r["errors"] else "FAIL"
        print(
            f"[{status}] {r['case']:15s} -> {r['consensus_label']} "
            f"({r['confidence']}) | {r['evidence_row_count']} evidence rows"
        )

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
