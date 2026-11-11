"""MAPLE runtime orchestrator — runs all 5 agents sequentially."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable, Optional

from maples.models import AnalysisState, AnalysisInput
from maples.runtime.state import add_audit

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
        from maples.runtime.retrieval_agent import run_retrieval_agent
        state.retrieval = await asyncio.to_thread(run_retrieval_agent, state.input)
        count = len(state.retrieval.retrieved_papers)
        add_audit(state, "Retrieval", f"Retrieved {count} papers", "; ".join(state.retrieval.audit_notes[:3]))

        await step("Evidence Extraction", f"Extracting marker-to-celltype associations from {count} papers…")
        from maples.runtime.evidence_agent import run_evidence_agent
        state.extraction = await asyncio.to_thread(run_evidence_agent, state.input, state.retrieval, llm)
        row_count = len(state.extraction.evidence_rows)
        add_audit(state, "Evidence", f"Found {row_count} evidence rows", "; ".join(state.extraction.audit_notes[:3]))

        await step("Candidate Agent", f"Grouping {row_count} evidence rows into candidate labels…")
        # Place every row in its field (organ / disease / species) and, when the
        # user named their own field, in a ring: home / near / far / unplaced.
        from maples.fields import classify_ring, home_from_input, normalize_context

        home = home_from_input(analysis_input.tissue, analysis_input.disease, analysis_input.species)
        state.home_field = home
        ring_counts: dict[str, int] = {}
        for row in state.extraction.evidence_rows:
            row.field = normalize_context(row.tissue, row.disease, row.species)
            row.ring = classify_ring(row.field, home)
            if row.ring:
                ring_counts[row.ring] = ring_counts.get(row.ring, 0) + 1
        fields_seen = len({r.field.key for r in state.extraction.evidence_rows})
        add_audit(
            state,
            "Fields",
            f"Evidence spans {fields_seen} field(s)"
            + (f" relative to {home.label}" if home else " (no home field given)"),
            ", ".join(f"{k}: {v}" for k, v in sorted(ring_counts.items())),
        )

        # Group different names for the same population before scoring candidates.
        from maples.runtime.label_agent import run_label_agent

        state.label_groups, label_notes = await asyncio.to_thread(
            run_label_agent, state.extraction.evidence_rows, llm
        )
        add_audit(state, "Labels", "; ".join(label_notes[-1:]), "; ".join(label_notes))

        from maples.runtime.candidate_agent import run_candidate_agent
        state.candidates = await asyncio.to_thread(run_candidate_agent, state.input, state.extraction)
        cand_count = len(state.candidates.candidate_labels)
        add_audit(state, "Candidates", f"Identified {cand_count} candidate labels", "; ".join(state.candidates.audit_notes[:3]))

        await step("Devil's Advocate", "Challenging the leading candidate…")
        from maples.runtime.devils_advocate_agent import run_devils_advocate_agent
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
        from maples.runtime.consensus_agent import run_consensus_agent
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
