"""MAPLE runtime orchestrator — runs all 5 agents sequentially."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable, Optional

from maple.models import AnalysisState, AnalysisInput
from maple.runtime.state import add_audit

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str], Awaitable[None]]


async def run_maple_pipeline(
    analysis_input: AnalysisInput,
    llm=None,
    progress_callback: Optional[ProgressCallback] = None,
) -> AnalysisState:
    """
    Run all 5 MAPLE agents in sequence.

    progress_callback(label, detail) is awaited before each agent to update the UI.
    """
    state = AnalysisState(input=analysis_input)

    async def step(label: str, detail: str = "") -> None:
        if progress_callback:
            await progress_callback(label, detail)

    try:
        await step("Retrieval Agent", f"Searching PubMed for {len(analysis_input.markers)} markers…")
        from maple.runtime.retrieval_agent import run_retrieval_agent
        state.retrieval = await asyncio.to_thread(run_retrieval_agent, state.input)
        count = len(state.retrieval.retrieved_papers)
        add_audit(state, "Retrieval", f"Retrieved {count} papers", "; ".join(state.retrieval.audit_notes[:3]))

        await step("Evidence Extraction", f"Extracting marker-to-celltype associations from {count} papers…")
        from maple.runtime.evidence_agent import run_evidence_agent
        state.extraction = await asyncio.to_thread(run_evidence_agent, state.input, state.retrieval, llm)
        row_count = len(state.extraction.evidence_rows)
        add_audit(state, "Evidence", f"Found {row_count} evidence rows", "; ".join(state.extraction.audit_notes[:3]))

        await step("Candidate Agent", f"Grouping {row_count} evidence rows into candidate labels…")
        from maple.runtime.candidate_agent import run_candidate_agent
        state.candidates = await asyncio.to_thread(run_candidate_agent, state.input, state.extraction)
        cand_count = len(state.candidates.candidate_labels)
        add_audit(state, "Candidates", f"Identified {cand_count} candidate labels", "; ".join(state.candidates.audit_notes[:3]))

        await step("Devil's Advocate", "Challenging the leading candidate…")
        from maple.runtime.devils_advocate_agent import run_devils_advocate_agent
        state.devils_advocate = await asyncio.to_thread(
            run_devils_advocate_agent, state.input, state.extraction, state.candidates, llm
        )
        add_audit(
            state,
            "Devil's Advocate",
            state.devils_advocate.critique_summary[:120],
            state.devils_advocate.strongest_counterargument[:200],
        )

        await step("Consensus Agent", "Producing final consensus…")
        from maple.runtime.consensus_agent import run_consensus_agent
        state.consensus = await asyncio.to_thread(
            run_consensus_agent,
            state.input,
            state.extraction,
            state.candidates,
            state.devils_advocate,
            llm,
        )
        add_audit(
            state,
            "Consensus",
            f"Label: {state.consensus.consensus_label} ({state.consensus.confidence})",
            state.consensus.consensus_rationale[:200],
        )

    except Exception as exc:
        logger.error("MAPLE pipeline error: %s", exc, exc_info=True)
        state.errors.append(f"Pipeline error: {exc}")
        add_audit(state, "Error", str(exc))

    return state
