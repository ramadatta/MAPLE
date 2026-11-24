"""Evidence Extraction Agent — LLM-first marker-to-celltype extraction.

The LLM reads each retrieved paper and decides whether the user's marker genes
are described as markers of a specific cell type. There are NO hardcoded cell
types, tissue priors, or label blocklists: the cell-type label, its specificity,
and the evidence type all come from the paper text as judged by the LLM.

Only generic, source-grounding validation is applied (genes must be a subset of
the user's panel, the snippet must be traceable to the paper text, and the genes
must appear in the snippet) so that citations stay faithful to the literature.

CRITICAL: Cell type labels MUST come from retrieved paper text only.
Never import marker_agent.py or marker_rules.json.
"""
from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from maples.models import (
    AnalysisInput,
    RetrievalResult,
    RetrievedPaper,
    EvidenceRow,
    ExtractionResult,
    ContextMatchDetail,
    EvidenceSection,
    EvidenceType,
    MatchStrength,
    CandidateSpecificity,
)
from pydantic import BaseModel, Field

from maples import config as cfg
from maples.extraction.validators import (
    validate_quote_fragments,
    validate_genes_in_text,
    validate_genes_in_snippet,
)

logger = logging.getLogger(__name__)

_ALLOWED_EVIDENCE_TYPES: frozenset[str] = frozenset({
    "direct_marker_celltype_assignment",
    "cluster_annotation",
    "differential_expression_only",
    "gene_mention_only",
})
_ALLOWED_SECTIONS: frozenset[str] = frozenset({
    "abstract", "results", "figure", "table", "supplement", "unknown",
})
_ALLOWED_SPECIFICITY: frozenset[str] = frozenset({"specific", "intermediate", "broad"})
_ALLOWED_STRENGTH: frozenset[str] = frozenset({"High", "Medium", "Low"})


def _paper_text(paper: RetrievedPaper) -> str:
    """Combined searchable text for a paper (title + abstract + full_text)."""
    parts = [paper.title or "", paper.abstract or "", paper.full_text or ""]
    return " ".join(p for p in parts if p)


def _find_user_genes_in_text(user_genes: list[str], text: str) -> list[str]:
    """User genes present in the text under their symbol or a known alias
    (case-insensitive, so mouse spellings like "Krt17" count)."""
    from maples.genes import genes_in_text

    return genes_in_text(user_genes, text)


def _build_context_match(
    paper: RetrievedPaper,
    analysis_input: AnalysisInput,
) -> ContextMatchDetail:
    """Record whether the paper text mentions any user-provided context terms."""
    text_lower = _paper_text(paper).lower()

    def _check(user_val: Optional[str]) -> str:
        if not user_val:
            return "not_provided"
        keywords = [w for w in user_val.lower().split() if len(w) > 3]
        if not keywords:
            return "unknown"
        matches = sum(1 for kw in keywords if kw in text_lower)
        return "matched" if matches >= max(1, len(keywords) // 2) else "unknown"

    return ContextMatchDetail(
        species=_check(analysis_input.species),
        tissue=_check(analysis_input.tissue),
        disease=_check(analysis_input.disease),
        technology=_check(analysis_input.technology),
    )


def _label_grounded_in_text(label: str, paper_text: str) -> bool:
    """Require the label's distinctive words to appear in the paper text."""
    generic = {"cell", "cells", "type", "types", "like", "state", "line", "lines", "the", "a"}
    tokens = [w for w in re.findall(r"[a-z0-9+]+", label.lower()) if len(w) > 2 and w not in generic]
    if not tokens:
        # Label was purely generic (e.g. "cells") — accept, snippet already validated.
        return True
    text_lower = paper_text.lower()
    hits = sum(1 for t in tokens if t in text_lower)
    return hits >= max(1, len(tokens) // 2)


def _normalize_enum(value: str, allowed: frozenset[str], default: str) -> str:
    v = (value or "").strip()
    if v in allowed:
        return v
    lowered = v.lower()
    for a in allowed:
        if a.lower() == lowered:
            return a
    return default


def _llm_extract(
    paper: RetrievedPaper,
    user_genes: list[str],
    analysis_input: AnalysisInput,
    llm,
) -> list[EvidenceRow]:
    """Extract evidence rows from a paper using the LLM. Returns [] on failure."""
    from maples.extraction.prompts import (
        EVIDENCE_EXTRACTION_SYSTEM,
        EVIDENCE_EXTRACTION_USER_TEMPLATE,
        EVIDENCE_EXTRACTION_FULLTEXT_PLACEHOLDER,
    )
    from maples.extraction.schemas import _LLMEvidenceOutput

    paper_text = _paper_text(paper)
    found_genes = _find_user_genes_in_text(user_genes, paper_text)
    if not found_genes:
        return []

    from maples.genes import canonical_symbol, describe_for_prompt, gene_in_text

    fulltext_section = ""
    if paper.full_text:
        if cfg.ENABLE_PASSAGE_SELECTION:
            from maples.extraction.passages import select_passages

            excerpt, _stats = select_passages(paper.full_text, found_genes, cfg.LLM_PASSAGE_CHARS)
        else:
            excerpt = paper.full_text[: cfg.LLM_FULLTEXT_CHARS]
        fulltext_section = EVIDENCE_EXTRACTION_FULLTEXT_PLACEHOLDER.format(full_text=excerpt)

    # Only the genes this paper actually mentions go into the prompt: a shorter,
    # more focused prompt, and the same paper + genes reuse the cached answer
    # across different panels.
    user_prompt = EVIDENCE_EXTRACTION_USER_TEMPLATE.format(
        pmid=paper.pmid,
        title=paper.title or "",
        abstract=(paper.abstract or "")[:3000],
        fulltext_section=fulltext_section,
        genes=describe_for_prompt(found_genes),
    )

    try:
        output = llm.complete_json(
            system=EVIDENCE_EXTRACTION_SYSTEM,
            user=user_prompt,
            schema=_LLMEvidenceOutput,
        )
    except Exception as exc:
        logger.warning("LLM extraction failed for PMID %s: %s", paper.pmid, exc)
        return []

    context = _build_context_match(paper, analysis_input)
    rows: list[EvidenceRow] = []

    for raw in output.rows:
        label = (raw.celltype_label or "").strip()
        snippet = (raw.evidence_snippet or "").strip()[:800]
        if not label or not snippet:
            continue

        # Genes must be a subset of the user's panel.
        valid_genes = [canonical_symbol(g, user_genes) for g in raw.matched_user_genes]
        valid_genes = list(dict.fromkeys(g for g in valid_genes if g))
        if not valid_genes:
            continue

        # Source grounding: snippet must be an exact (searchable) quote from the
        # paper, and the label must be named in the paper text.
        if not validate_quote_fragments(snippet, paper_text):
            continue
        if not _label_grounded_in_text(label, paper_text):
            continue

        # Keep only the genes that literally appear in the quoted passage, rather
        # than rejecting the whole row — this preserves a multi-sentence marker
        # panel while dropping any gene the quote does not actually support.
        valid_genes = [g for g in valid_genes if gene_in_text(g, snippet)]
        if not valid_genes:
            continue

        section: EvidenceSection = _normalize_enum(  # type: ignore[assignment]
            raw.evidence_section, _ALLOWED_SECTIONS, "unknown"
        )
        evidence_type: EvidenceType = _normalize_enum(  # type: ignore[assignment]
            raw.evidence_type, _ALLOWED_EVIDENCE_TYPES, "direct_marker_celltype_assignment"
        )
        specificity: CandidateSpecificity = _normalize_enum(  # type: ignore[assignment]
            raw.specificity, _ALLOWED_SPECIFICITY, "intermediate"
        )
        strength: MatchStrength = _normalize_enum(  # type: ignore[assignment]
            raw.match_strength, _ALLOWED_STRENGTH, "Medium" if len(valid_genes) >= 2 else "Low"
        )
        normalized = (raw.normalized_label or label).strip().lower()

        def _clean_ctx(val: str) -> Optional[str]:
            v = (val or "").strip()
            if not v or v.lower() in {"n/a", "na", "none", "not stated", "unknown", "unspecified"}:
                return None
            return v[:80]

        rows.append(
            EvidenceRow(
                pmid=paper.pmid,
                pmcid=paper.pmcid,
                paper_title=paper.title or "",
                journal=paper.journal,
                publication_date=paper.publication_date,
                publication_year=paper.publication_year,
                celltype_label=label,
                normalized_label=normalized,
                marker_specific=bool(raw.marker_specific),
                specificity=specificity,
                matched_user_genes=valid_genes,
                number_of_user_genes_found=len(valid_genes),
                evidence_snippet=snippet[:800],
                evidence_section=section,
                match_strength=strength,
                evidence_type=evidence_type,
                match_reason=(
                    f"LLM ({evidence_type}): {len(valid_genes)} gene(s) with '{label}'"
                ),
                context_match=context,
                tissue=_clean_ctx(getattr(raw, "tissue", "")),
                disease=_clean_ctx(getattr(raw, "disease", "")),
                species=_clean_ctx(getattr(raw, "species", "")),
                source_url=paper.source_url,
            )
        )

    return _dedupe_rows(rows)


def _dedupe_rows(rows: list[EvidenceRow]) -> list[EvidenceRow]:
    """Keep the strongest row per (pmid, normalized label)."""
    best: dict[tuple[str, str], EvidenceRow] = {}
    for row in rows:
        key = (row.pmid, row.normalized_label or row.celltype_label.lower())
        cur = best.get(key)
        if cur is None or row.number_of_user_genes_found > cur.number_of_user_genes_found:
            best[key] = row
    return list(best.values())


def run_evidence_agent(
    analysis_input: AnalysisInput,
    retrieval_result: RetrievalResult,
    llm=None,
    on_progress=None,
) -> ExtractionResult:
    """Extract EvidenceRow objects from retrieved papers using the LLM.

    ``on_progress(papers_done, papers_total, new_rows)`` is called after each paper
    is read, so callers can show results as they arrive. It must be quick and
    must not raise; errors in it are ignored."""
    user_genes = analysis_input.markers
    papers = retrieval_result.retrieved_papers
    audit_notes: list[str] = []
    excluded_reasons: list[str] = []
    excluded_count = 0
    all_rows: list[EvidenceRow] = []

    use_llm = llm is not None and cfg.ENABLE_LLM_EXTRACTION
    llm_budget = cfg.LLM_EXTRACTION_MAX_PAPERS if use_llm else 0
    llm_used = 0

    abstract_only = 0
    papers_direct = papers_cluster = papers_de_only = papers_gene_only = 0
    papers_no_assignment = 0

    if not use_llm:
        audit_notes.append(
            "LLM extraction unavailable — no evidence extracted. "
            "Provide an API key so the model can read the papers."
        )
        return ExtractionResult(
            evidence_rows=[],
            excluded_paper_count=len(papers),
            excluded_reasons=[
                f"PMID {p.pmid}: skipped (LLM extractor disabled)" for p in papers[:20]
            ],
            audit_notes=audit_notes,
            abstract_only_papers=sum(1 for p in papers if not p.full_text),
        )

    abstract_only = sum(1 for p in papers if not p.full_text)

    # ── Pass 1: select papers eligible for an LLM call (genes present, in budget) ──
    candidates: list[tuple[RetrievedPaper, list[str]]] = []
    for paper in papers:
        found_genes = _find_user_genes_in_text(user_genes, _paper_text(paper))
        if not found_genes:
            excluded_count += 1
            excluded_reasons.append(
                f"PMID {paper.pmid}: no user-provided genes found in title/abstract/full_text"
            )
            continue
        candidates.append((paper, found_genes))

    # Single-cell pool papers have their own reading slots (on top of the regular
    # budget), so the focused single-cell literature is not crowded out.
    pool_candidates = [c for c in candidates if c[0].in_pool]
    candidates = [c for c in candidates if not c[0].in_pool]
    pool_chosen, pool_screened_out, pool_not_read = _choose_pool_papers(
        pool_candidates, user_genes, llm, audit_notes
    )

    # When the user named their own field, alternate between papers that look like
    # their field and papers from other fields, so neither crowds out the other
    # within the reading budget. Rank order is kept inside each side.
    from maples.fields import home_from_input, text_matches_home

    home = home_from_input(analysis_input.tissue, analysis_input.disease, analysis_input.species)
    if home is not None and len(candidates) > llm_budget:
        home_side = [c for c in candidates if text_matches_home(f"{c[0].title} {c[0].abstract or ''}", home)]
        other_side = [c for c in candidates if not any(c is h for h in home_side)]
        balanced: list[tuple[RetrievedPaper, list[str]]] = []
        while home_side or other_side:
            if home_side:
                balanced.append(home_side.pop(0))
            if other_side:
                balanced.append(other_side.pop(0))
        candidates = balanced
        audit_notes.append("Reading budget split between your field and other fields")

    eligible: list[RetrievedPaper] = []
    eligible_found: list[list[str]] = []
    for paper, found_genes in candidates:
        if len(eligible) >= llm_budget:
            excluded_count += 1
            excluded_reasons.append(
                f"PMID {paper.pmid}: genes found but LLM budget "
                f"({llm_budget} papers) exhausted"
            )
            continue
        eligible.append(paper)
        eligible_found.append(found_genes)

    for paper, found_genes in pool_chosen:
        eligible.append(paper)
        eligible_found.append(found_genes)

    # ── Pass 2: run per-paper extraction concurrently (bounded thread pool) ──────
    results: dict[int, list[EvidenceRow]] = {}
    if eligible:
        max_workers = max(1, min(len(eligible), cfg.EVIDENCE_EXTRACTION_CONCURRENCY))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(_llm_extract, paper, user_genes, analysis_input, llm): idx
                for idx, paper in enumerate(eligible)
            }
            for done, future in enumerate(as_completed(future_map), 1):
                idx = future_map[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    logger.warning(
                        "LLM extraction error for PMID %s: %s", eligible[idx].pmid, exc
                    )
                    results[idx] = []
                if on_progress is not None:
                    try:
                        on_progress(done, len(eligible), results[idx])
                    except Exception:  # progress display must never break extraction
                        pass
    llm_used = len(eligible)

    # ── Pass 3: aggregate in stable paper order ──────────────────────────────────
    for idx, paper in enumerate(eligible):
        paper_rows = results.get(idx, [])
        if not paper_rows:
            papers_no_assignment += 1
            excluded_count += 1
            excluded_reasons.append(
                f"PMID {paper.pmid}: genes found ({', '.join(eligible_found[idx])}) "
                "but the model found no explicit cell-type assignment"
            )
            continue

        # Per-paper evidence-type tally uses the strongest row.
        best_type = max(
            (r.evidence_type for r in paper_rows),
            key=lambda t: _evidence_type_rank(t),
        )
        if best_type == "direct_marker_celltype_assignment":
            papers_direct += 1
        elif best_type == "cluster_annotation":
            papers_cluster += 1
        elif best_type == "differential_expression_only":
            papers_de_only += 1
        else:
            papers_gene_only += 1

        all_rows.extend(paper_rows)

    audit_notes.append(
        f"Processed {len(papers)} papers with the LLM extractor "
        f"({llm_used} model calls): {len(all_rows)} evidence rows, "
        f"{excluded_count} papers excluded"
    )
    audit_notes.append(
        f"Evidence signals: direct assignment={papers_direct}, cluster={papers_cluster}, "
        f"DE-only={papers_de_only}, gene-mention-only={papers_gene_only}, "
        f"no assignment={papers_no_assignment}"
    )

    # Sort by evidence type, gene count, then match strength — all LLM-derived.
    _strength_order = {"High": 3, "Medium": 2, "Low": 1}
    all_rows.sort(
        key=lambda r: (
            _evidence_type_rank(r.evidence_type),
            r.number_of_user_genes_found,
            _strength_order.get(r.match_strength, 0),
        ),
        reverse=True,
    )

    return ExtractionResult(
        evidence_rows=all_rows,
        excluded_paper_count=excluded_count,
        excluded_reasons=excluded_reasons[:20],
        audit_notes=audit_notes,
        papers_with_direct_assignment=papers_direct,
        papers_with_cluster_annotation=papers_cluster,
        papers_de_only=papers_de_only,
        papers_gene_mention_only=papers_gene_only,
        papers_no_celltype_assignment=papers_no_assignment,
        abstract_only_papers=abstract_only,
        validation_rejected_rows=0,
        pool_read=[p.pmid for p, _ in pool_chosen],
        pool_screened_out=pool_screened_out,
        pool_not_read=pool_not_read,
    )


SKIM_SYSTEM = (
    "You screen paper abstracts for a literature tool. For each numbered abstract, decide "
    "whether it describes at least one of the listed genes as expressed in, enriched in, or "
    "marking a specific cell type, cell subpopulation or cell state (from single-cell, spatial, "
    "imaging or other data). Answer NO for abstracts that only mention the genes in passing, "
    "study signalling or drugs without tying the genes to a cell type, or are about something "
    "else. When unsure, answer YES: a later step reads the full paper."
)
SKIM_USER = (
    "Genes: {genes}\n\nAbstracts:\n{items}\n\n"
    'Return JSON: {{"relevant": [numbers of the abstracts that qualify]}}'
)


class _SkimOut(BaseModel):
    relevant: list[int] = Field(default_factory=list)


def _skim_pool(llm, papers: list, user_genes: list[str]) -> set[str]:
    """PMIDs whose abstracts tie the genes to a cell type. A batch that fails is kept."""
    from maples.genes import describe_for_prompt

    size = max(1, cfg.SC_POOL_SKIM_BATCH)
    batches = [papers[i:i + size] for i in range(0, len(papers), size)]

    def run(batch) -> set[str]:
        items = "\n".join(
            f"{n}. {p.title} — {(p.abstract or '')[:1200]}" for n, p in enumerate(batch, 1)
        )
        try:
            out = llm.complete_json(
                system=SKIM_SYSTEM,
                user=SKIM_USER.format(genes=describe_for_prompt(user_genes), items=items),
                schema=_SkimOut,
            )
            return {batch[n - 1].pmid for n in out.relevant if isinstance(n, int) and 1 <= n <= len(batch)}
        except Exception as exc:  # never lose papers because a skim call failed
            logger.warning("Abstract skim failed for a batch; keeping it: %s", exc)
            return {p.pmid for p in batch}

    keep: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(8, len(batches)) or 1) as pool:
        for part in pool.map(run, batches):
            keep |= part
    return keep


def _choose_pool_papers(pool_candidates, user_genes, llm, audit_notes):
    """Pick which single-cell pool papers get a full read.

    Up to SC_POOL_READ_MAX are read. A bigger pool is skimmed first: the AI reads the
    abstracts in batches and only those tying the genes to a cell type go forward.
    Returns (chosen [(paper, genes)], screened-out PMIDs, not-read PMIDs).
    """
    if not pool_candidates:
        return [], [], []
    limit = max(0, cfg.SC_POOL_READ_MAX)
    screened_out: list[str] = []
    kept = pool_candidates
    if len(pool_candidates) > limit and llm is not None:
        from maples.runtime import activity

        activity.report("phase", text=f"Skimming {len(pool_candidates)} single-cell abstracts")
        relevant = _skim_pool(llm, [p for p, _ in pool_candidates], user_genes)
        kept = [c for c in pool_candidates if c[0].pmid in relevant]
        screened_out = [p.pmid for p, _ in pool_candidates if p.pmid not in relevant]
        audit_notes.append(
            f"Skimmed {len(pool_candidates)} single-cell pool abstracts: {len(kept)} tie the genes "
            f"to a cell type, {len(screened_out)} screened out"
        )
    chosen, not_read = kept[:limit], [p.pmid for p, _ in kept[limit:]]
    # full text for the chosen ones that do not have it yet (abstract skim needed none)
    need = [p for p, _ in chosen if not p.full_text]
    if need and cfg.ENABLE_FULLTEXT:
        try:
            from maples.literature.pmc import enrich_with_fulltext

            enrich_with_fulltext(need, max_papers=len(need))
        except Exception as exc:
            logger.warning("Full text for single-cell pool papers failed: %s", exc)
    audit_notes.append(
        f"Single-cell pool: reading {len(chosen)} paper(s)"
        + (f"; {len(not_read)} left unread (reading slots full)" if not_read else "")
    )
    return chosen, screened_out, not_read


def _evidence_type_rank(evidence_type: str) -> int:
    return {
        "direct_marker_celltype_assignment": 5,
        "cluster_annotation": 4,
        "differential_expression_only": 2,
        "gene_mention_only": 1,
    }.get(evidence_type, 0)
