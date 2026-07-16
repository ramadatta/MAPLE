"""Public programmatic API for MAPLE.

Import this to run MAPLE from a script, notebook, or another service without the
Chainlit UI:

    from maple import annotate

    result = annotate(
        markers=["COL1A1", "COL3A1", "POSTN", "CTHRC1"],
        tissue="lung",
        disease="idiopathic pulmonary fibrosis",
        species="human",
    )
    print(result.label, result.confidence)
    for row in result.evidence:
        print(row.celltype_label, row.pmid)

``annotate_async`` is the canonical (async) entry point; ``annotate`` is a
loop-safe synchronous convenience wrapper. Batch several marker sets at once
with ``annotate_marker_sets``. The returned ``AnnotationResult`` is a stable
public contract — internal agent state is only exposed via ``raw_state`` when
explicitly requested, so the pipeline internals can be refactored freely.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import subprocess
from datetime import datetime, timezone
from typing import Awaitable, Mapping, Optional, Sequence, TypeVar, Union

from pydantic import BaseModel, Field

from maple.models import AnalysisInput, AnalysisState, ConsensusAlternative, EvidenceRow

__all__ = [
    "annotate",
    "annotate_async",
    "annotate_marker_sets",
    "annotate_marker_sets_async",
    "AnnotationResult",
    "RunMetadata",
]

MarkerInput = Union[str, Sequence[str]]
_T = TypeVar("_T")


def _maple_version() -> str:
    try:
        from importlib.metadata import version

        return version("maple")
    except Exception:
        return "0.2.0-beta"


def _code_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            timeout=3,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


# ─── Public result contract ───────────────────────────────────────────────────

class RunMetadata(BaseModel):
    """Provenance for a single annotation run (reproducibility / auditing)."""

    maple_version: str = ""
    code_sha: str = ""
    prompt_version: str = ""
    model: Optional[str] = None
    provider: Optional[str] = None
    llm_used: bool = False
    cache_enabled: bool = True
    retrieved_at: str = ""  # ISO-8601 UTC timestamp
    marker_count: int = 0
    tissue: Optional[str] = None
    disease: Optional[str] = None
    species: Optional[str] = None
    papers_per_gene: int = 0
    llm_extraction_max_papers: int = 0


class AnnotationResult(BaseModel):
    """Stable public result object for one marker set."""

    label: str = "Insufficient evidence"
    confidence: str = "Insufficient"
    rationale: str = ""
    supporting_genes: list[str] = Field(default_factory=list)
    supporting_pmids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRow] = Field(default_factory=list)
    alternatives: list[ConsensusAlternative] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    run_metadata: RunMetadata = Field(default_factory=RunMetadata)
    # Full internal pipeline state — only populated when include_raw_state=True.
    raw_state: Optional[AnalysisState] = None

    def to_dict(self, include_raw: bool = False) -> dict:
        data = self.model_dump(mode="json", exclude={"raw_state"})
        if include_raw and self.raw_state is not None:
            data["raw_state"] = self.raw_state.model_dump(mode="json")
        return data

    def to_json(self, *, indent: int = 2, include_raw: bool = False) -> str:
        import json

        return json.dumps(self.to_dict(include_raw=include_raw), indent=indent)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_markers(markers: MarkerInput) -> list[str]:
    """Accept a list/tuple of symbols or a raw string in any common format."""
    from utils.gene_parser import parse_genes

    raw = markers if isinstance(markers, str) else ", ".join(str(m) for m in markers)
    genes, _warnings = parse_genes(raw)
    return genes


def _make_llm_from_env():
    """Create an LLM client from environment config, or None (degraded mode)."""
    if os.getenv("DISABLE_LLM", "").lower() in ("1", "true", "yes"):
        return None
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GITLAB_TOKEN")
    if not api_key:
        return None
    try:
        from services.llm_service import create_llm_service

        provider = os.getenv("LLM_PROVIDER", "auto").lower()
        provider = None if provider == "auto" else provider
        return create_llm_service(api_key, provider=provider)
    except Exception:
        return None


def _model_provider(llm) -> tuple[Optional[str], Optional[str]]:
    if llm is None:
        return None, None
    model = getattr(llm, "model", None)
    provider = type(llm).__name__.replace("LLMService", "") or None
    return model, provider


def _state_to_result(
    state: AnalysisState,
    llm,
    include_raw_state: bool,
) -> AnnotationResult:
    from maple import config as cfg

    try:
        from maple.extraction.prompts import PROMPT_VERSION
    except Exception:
        PROMPT_VERSION = ""

    consensus = state.consensus
    evidence = list(state.extraction.evidence_rows) if state.extraction else []

    warnings: list[str] = list(state.errors)
    if state.devils_advocate:
        warnings.extend(state.devils_advocate.context_mismatches)
    if not evidence:
        warnings.append("No evidence rows were extracted from the retrieved literature.")

    model, provider = _model_provider(llm)
    meta = RunMetadata(
        maple_version=_maple_version(),
        code_sha=_code_sha(),
        prompt_version=PROMPT_VERSION,
        model=model,
        provider=provider,
        llm_used=llm is not None,
        cache_enabled=os.getenv("MAPLE_ENABLE_LLM_CACHE", "true").lower()
        not in ("0", "false", "no"),
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        marker_count=len(state.input.markers),
        tissue=state.input.tissue,
        disease=state.input.disease,
        species=state.input.species,
        papers_per_gene=cfg.PAPERS_PER_GENE,
        llm_extraction_max_papers=cfg.LLM_EXTRACTION_MAX_PAPERS,
    )

    return AnnotationResult(
        label=consensus.consensus_label if consensus else "Insufficient evidence",
        confidence=consensus.confidence if consensus else "Insufficient",
        rationale=consensus.consensus_rationale if consensus else "",
        supporting_genes=list(consensus.supporting_genes) if consensus else [],
        supporting_pmids=list(consensus.main_supporting_pmids) if consensus else [],
        evidence=evidence,
        alternatives=list(consensus.alternative_labels) if consensus else [],
        warnings=warnings,
        run_metadata=meta,
        raw_state=state if include_raw_state else None,
    )


def _run_sync(coro: Awaitable[_T]) -> _T:
    """Run a coroutine to completion whether or not a loop is already running."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is not None and running.is_running():
        # Inside an existing loop (e.g. Jupyter) — run in a dedicated thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


# ─── Single annotation ─────────────────────────────────────────────────────────

async def annotate_async(
    markers: MarkerInput,
    *,
    tissue: Optional[str] = None,
    disease: Optional[str] = None,
    species: Optional[str] = None,
    technology: Optional[str] = None,
    llm=None,
    include_raw_state: bool = False,
    progress_callback=None,
) -> AnnotationResult:
    """
    Annotate one marker set against the literature. Canonical (async) entry point.

    Args:
        markers: gene symbols as a list/tuple, or a raw string in any common format.
        tissue/disease/species/technology: optional context that focuses retrieval.
        llm: an LLM client; if None, one is built from environment config
            (OPENAI_API_KEY / GITLAB_TOKEN). Without a key, runs in degraded mode
            (no evidence extraction).
        include_raw_state: attach the full internal AnalysisState to the result.
        progress_callback: optional async callback(label, detail) for progress.

    Returns:
        AnnotationResult
    """
    genes = _normalize_markers(markers)
    if not genes:
        raise ValueError("No valid gene symbols found in `markers`.")

    analysis_input = AnalysisInput(
        markers=genes,
        tissue=tissue or None,
        disease=disease or None,
        species=species or None,
        technology=technology or None,
    )

    if llm is None:
        llm = _make_llm_from_env()

    from maple.runtime.orchestrator import run_maple_pipeline

    state = await run_maple_pipeline(
        analysis_input, llm=llm, progress_callback=progress_callback
    )
    return _state_to_result(state, llm, include_raw_state)


def annotate(
    markers: MarkerInput,
    *,
    tissue: Optional[str] = None,
    disease: Optional[str] = None,
    species: Optional[str] = None,
    technology: Optional[str] = None,
    llm=None,
    include_raw_state: bool = False,
) -> AnnotationResult:
    """Synchronous convenience wrapper around :func:`annotate_async`."""
    return _run_sync(
        annotate_async(
            markers,
            tissue=tissue,
            disease=disease,
            species=species,
            technology=technology,
            llm=llm,
            include_raw_state=include_raw_state,
        )
    )


# ─── Batch annotation (generic, dependency-light) ──────────────────────────────

async def annotate_marker_sets_async(
    markers_by_group: Mapping[str, MarkerInput],
    *,
    tissue: Optional[str] = None,
    disease: Optional[str] = None,
    species: Optional[str] = None,
    technology: Optional[str] = None,
    llm=None,
    max_concurrent: int = 2,
    include_raw_state: bool = False,
) -> dict[str, AnnotationResult]:
    """
    Annotate many marker sets (e.g. one per cluster). Failures are isolated per
    group: a group that errors yields an AnnotationResult with the error in
    ``warnings`` rather than aborting the whole batch.

    ``max_concurrent`` bounds how many groups run at once — keep it modest, since
    each group runs a full pipeline that hits PubMed/NCBI and the LLM.
    """
    if llm is None:
        llm = _make_llm_from_env()

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(group: str, markers: MarkerInput) -> tuple[str, AnnotationResult]:
        async with semaphore:
            try:
                res = await annotate_async(
                    markers,
                    tissue=tissue,
                    disease=disease,
                    species=species,
                    technology=technology,
                    llm=llm,
                    include_raw_state=include_raw_state,
                )
                return group, res
            except Exception as exc:  # per-group failure isolation
                return group, AnnotationResult(
                    warnings=[f"Annotation failed for group '{group}': {exc}"],
                    run_metadata=RunMetadata(
                        maple_version=_maple_version(), code_sha=_code_sha()
                    ),
                )

    pairs = await asyncio.gather(
        *(_one(group, markers) for group, markers in markers_by_group.items())
    )
    return dict(pairs)


def annotate_marker_sets(
    markers_by_group: Mapping[str, MarkerInput],
    *,
    tissue: Optional[str] = None,
    disease: Optional[str] = None,
    species: Optional[str] = None,
    technology: Optional[str] = None,
    llm=None,
    max_concurrent: int = 2,
    include_raw_state: bool = False,
) -> dict[str, AnnotationResult]:
    """Synchronous convenience wrapper around :func:`annotate_marker_sets_async`."""
    return _run_sync(
        annotate_marker_sets_async(
            markers_by_group,
            tissue=tissue,
            disease=disease,
            species=species,
            technology=technology,
            llm=llm,
            max_concurrent=max_concurrent,
            include_raw_state=include_raw_state,
        )
    )
