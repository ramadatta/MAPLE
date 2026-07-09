"""Rule-based evidence signal scoring for MAPLE Phase 1.

Scores text chunks by marker-to-celltype assignment language rather than
gene mention count alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from maple import config as cfg
from maple.extraction.validators import validate_genes_in_text
from maple.models import AnalysisInput, EvidenceSection, MatchStrength, RetrievedPaper

EvidenceType = Literal[
    "direct_marker_celltype_assignment",
    "cluster_annotation",
    "differential_expression_only",
    "gene_mention_only",
    "celltype_mention_only",
    "unrelated",
]

# Strong marker-to-celltype assignment phrases
ASSIGNMENT_PHRASES: tuple[str, ...] = (
    "annotated as",
    "identified as",
    "defined as",
    "classified as",
    "assigned as",
    "characterized by",
    "marked by",
    "markers of",
    "marker genes for",
    "marker gene for",
    "expressed markers",
    "signature genes",
    "canonical markers",
    "based on expression of",
    "termed ",
    "we termed",
    "named ",
    "population that we",
    "cell population",
    "cell type",
    "cell state",
)

CLUSTER_PHRASES: tuple[str, ...] = (
    "cluster was",
    "cluster is",
    "cluster were",
    "subcluster",
    "cluster ",
    "clustered as",
    "umap",
    "t-sne",
    "tsne",
)

# scRNA / spatial relevance (shared with retrieval ranking)
SCRNA_PHRASES: tuple[str, ...] = (
    "single-cell",
    "single cell",
    "scrna-seq",
    "scrna seq",
    "scrna",
    "snrna-seq",
    "snrna seq",
    "spatial transcriptomics",
    "visium",
    "merfish",
    "slide-seq",
    "cell atlas",
    "differential expression",
    "marker genes",
    "10x genomics",
    "dropseq",
    "drop-seq",
    "seurat",
    "scanpy",
    "single nucleus",
)

DE_ONLY_PHRASES: tuple[str, ...] = (
    "upregulated",
    "downregulated",
    "differentially expressed",
    "increased expression",
    "decreased expression",
    "enriched genes",
    "degs",
    "deg ",
)

CELLTYPE_HINTS: tuple[str, ...] = (
    " cell",
    " cells",
    "cyte",
    "blast",
    "phage",
    "ocyte",
    "fibroblast",
    "macrophage",
    "epithelial",
    "basaloid",
    "cluster",
    "subcluster",
    "lineage",
)

EMITTABLE_EVIDENCE_TYPES: frozenset[str] = frozenset({
    "direct_marker_celltype_assignment",
    "cluster_annotation",
    "differential_expression_only",
})

STRONG_EVIDENCE_TYPES: frozenset[str] = frozenset({
    "direct_marker_celltype_assignment",
    "cluster_annotation",
})


@dataclass
class EvidenceSignal:
    paper_id: str
    chunk_id: str
    text: str
    detected_user_genes: list[str] = field(default_factory=list)
    detected_celltype_terms: list[str] = field(default_factory=list)
    annotation_phrase_hits: list[str] = field(default_factory=list)
    scrna_phrase_hits: list[str] = field(default_factory=list)
    evidence_type: EvidenceType = "unrelated"
    assignment_score: float = 0.0
    reason: str = ""


def genes_in_text(user_genes: list[str], text: str) -> list[str]:
    """Return user genes found as whole-word tokens."""
    return validate_genes_in_text(user_genes, text)


def _find_phrase_hits(text_lower: str, phrases: tuple[str, ...]) -> list[str]:
    return [p for p in phrases if p in text_lower]


def _detect_celltype_terms(text: str) -> list[str]:
    """Extract coarse cell-type-like phrases from chunk text."""
    terms: list[str] = []
    seen: set[str] = set()

    for cm in re.finditer(r"\bcells?\b", text, re.IGNORECASE):
        before = text[: cm.start()].rstrip()
        if not before:
            continue
        words = before.split()
        for n in range(min(4, len(words)), 0, -1):
            phrase = " ".join(words[-n:]).strip()
            if len(phrase) < 4:
                continue
            key = phrase.lower()
            if key not in seen and any(h in key for h in CELLTYPE_HINTS):
                seen.add(key)
                terms.append(phrase + (" cells" if "cell" not in key else ""))
                break

    for m in re.finditer(r"\b(cluster\s*\d+[a-z]?)\b", text, re.IGNORECASE):
        val = m.group(1).strip()
        key = val.lower()
        if key not in seen:
            seen.add(key)
            terms.append(val)

    return terms[:8]


def _genes_near_celltype(text_lower: str, genes: list[str], celltype_terms: list[str]) -> bool:
    """True when at least one gene and one celltype term appear within ~120 chars."""
    if not genes or not celltype_terms:
        return False
    for gene in genes:
        for m in re.finditer(rf"\b{re.escape(gene.upper())}\b", text_lower.upper()):
            start = max(0, m.start() - 120)
            end = min(len(text_lower), m.end() + 120)
            window = text_lower[start:end]
            if any(ct.lower() in window for ct in celltype_terms):
                return True
    return False


def _context_phrase_hits(text_lower: str, analysis_input: AnalysisInput | None) -> list[str]:
    if not analysis_input or not (cfg.USE_USER_CONTEXT or analysis_input.has_context):
        return []
    hits: list[str] = []
    for val in (analysis_input.tissue, analysis_input.disease, analysis_input.species):
        if not val:
            continue
        for kw in [w for w in val.lower().split() if len(w) > 3]:
            if kw in text_lower:
                hits.append(kw)
    return hits


def classify_evidence_type(
    text: str,
    user_genes: list[str],
    assignment_hits: list[str],
    cluster_hits: list[str],
    de_hits: list[str],
    celltype_terms: list[str],
    detected_genes: list[str],
) -> EvidenceType:
    text_lower = text.lower()
    has_genes = bool(detected_genes)
    has_celltype = bool(celltype_terms) or any(h in text_lower for h in CELLTYPE_HINTS)

    if has_genes and (assignment_hits or cluster_hits) and has_celltype:
        if cluster_hits and any(p in text_lower for p in ("cluster", "subcluster")):
            return "cluster_annotation"
        if assignment_hits:
            return "direct_marker_celltype_assignment"
        return "cluster_annotation"

    if has_genes and cluster_hits and _genes_near_celltype(text_lower, detected_genes, celltype_terms or ["cluster"]):
        return "cluster_annotation"

    if has_genes and de_hits and not assignment_hits and not cluster_hits:
        return "differential_expression_only"

    if has_genes and not has_celltype and not assignment_hits:
        return "gene_mention_only"

    if has_celltype and not has_genes:
        return "celltype_mention_only"

    if has_genes and has_celltype:
        return "direct_marker_celltype_assignment"

    return "unrelated"


def _score_from_type(
    evidence_type: EvidenceType,
    gene_count: int,
    assignment_hits: list[str],
    cluster_hits: list[str],
    scrna_hits: list[str],
    proximity: bool,
    context_hits: list[str],
) -> float:
    base = {
        "direct_marker_celltype_assignment": 0.85,
        "cluster_annotation": 0.75,
        "differential_expression_only": 0.35,
        "gene_mention_only": 0.15,
        "celltype_mention_only": 0.10,
        "unrelated": 0.0,
    }.get(evidence_type, 0.0)

    score = base
    score += min(gene_count, 6) * 0.04
    score += 0.05 * len(assignment_hits)
    score += 0.04 * len(cluster_hits)
    score += 0.03 * min(len(scrna_hits), 3)
    if proximity:
        score += 0.08
    if context_hits:
        score += 0.05 * min(len(context_hits), 2)
    return min(score, 1.0)


def score_evidence_chunk(
    chunk_text: str,
    user_genes: list[str],
    analysis_input: AnalysisInput | None,
    *,
    paper_id: str = "",
    chunk_id: str = "",
) -> EvidenceSignal:
    """Score a single text chunk for marker-to-celltype assignment evidence."""
    text = (chunk_text or "").strip()
    text_lower = text.lower()
    detected_genes = genes_in_text(user_genes, text)
    celltype_terms = _detect_celltype_terms(text)
    assignment_hits = _find_phrase_hits(text_lower, ASSIGNMENT_PHRASES)
    cluster_hits = _find_phrase_hits(text_lower, CLUSTER_PHRASES)
    scrna_hits = _find_phrase_hits(text_lower, SCRNA_PHRASES)
    de_hits = _find_phrase_hits(text_lower, DE_ONLY_PHRASES)
    context_hits = _context_phrase_hits(text_lower, analysis_input)

    evidence_type = classify_evidence_type(
        text, user_genes, assignment_hits, cluster_hits, de_hits, celltype_terms, detected_genes
    )
    proximity = _genes_near_celltype(text_lower, detected_genes, celltype_terms)
    assignment_score = _score_from_type(
        evidence_type,
        len(detected_genes),
        assignment_hits,
        cluster_hits,
        scrna_hits,
        proximity,
        context_hits,
    )

    reason_parts: list[str] = []
    if assignment_hits:
        reason_parts.append(f"assignment phrase: '{assignment_hits[0]}'")
    if cluster_hits:
        reason_parts.append(f"cluster language: '{cluster_hits[0]}'")
    if detected_genes:
        reason_parts.append(f"{len(detected_genes)} submitted gene(s)")
    if celltype_terms:
        reason_parts.append(f"celltype terms: {celltype_terms[0][:40]}")
    if not reason_parts:
        reason_parts.append(evidence_type.replace("_", " "))

    return EvidenceSignal(
        paper_id=paper_id,
        chunk_id=chunk_id,
        text=text,
        detected_user_genes=detected_genes,
        detected_celltype_terms=celltype_terms,
        annotation_phrase_hits=assignment_hits + cluster_hits,
        scrna_phrase_hits=scrna_hits,
        evidence_type=evidence_type,
        assignment_score=assignment_score,
        reason="; ".join(reason_parts),
    )


def split_paper_into_chunks(paper: RetrievedPaper) -> list[tuple[str, str]]:
    """Split paper text into scored chunks (sentence windows)."""
    chunks: list[tuple[str, str]] = []
    abstract = (paper.abstract or "").strip()
    full_text = (paper.full_text or "").strip()

    def _sentence_windows(text: str, prefix: str, window: int) -> None:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        n = len(sentences)
        for i in range(n):
            idxs = range(max(0, i - window), min(n, i + window + 1))
            chunk = " ".join(sentences[j] for j in idxs).strip()
            if len(chunk) >= 30:
                chunks.append((f"{prefix}_w{i}", chunk))

    if abstract:
        _sentence_windows(abstract, "abstract", window=1)
    if full_text:
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", full_text) if len(p.strip()) >= 40]
        for pi, para in enumerate(paragraphs[:40]):
            if len(para) > 600:
                _sentence_windows(para, f"ft_p{pi}", window=1)
            else:
                chunks.append((f"ft_p{pi}", para))

    if not chunks and abstract:
        chunks.append(("abstract_full", abstract[:1200]))
    return chunks


def score_paper_signals(
    paper: RetrievedPaper,
    user_genes: list[str],
    analysis_input: AnalysisInput | None,
) -> list[EvidenceSignal]:
    """Score all chunks for a paper; sorted by assignment_score descending."""
    signals: list[EvidenceSignal] = []
    for chunk_id, text in split_paper_into_chunks(paper):
        sig = score_evidence_chunk(
            text,
            user_genes,
            analysis_input,
            paper_id=paper.pmid,
            chunk_id=chunk_id,
        )
        if sig.detected_user_genes or sig.evidence_type != "unrelated":
            signals.append(sig)
    signals.sort(key=lambda s: s.assignment_score, reverse=True)
    return signals


def best_paper_signal(signals: list[EvidenceSignal]) -> EvidenceSignal | None:
    if not signals:
        return None
    strong = [s for s in signals if s.evidence_type in STRONG_EVIDENCE_TYPES]
    return strong[0] if strong else signals[0]


def paper_rank_score(
    paper: RetrievedPaper,
    user_genes: list[str],
    analysis_input: AnalysisInput | None,
) -> tuple[float, str]:
    """
    Evidence-signal-aware paper score for retrieval ranking.
    Returns (score, human-readable retrieval_reason).
    """
    combined = " ".join(filter(None, [paper.title or "", paper.abstract or "", paper.full_text or ""]))
    gene_count = len(genes_in_text(user_genes, combined))
    if gene_count == 0:
        return 0.0, "No submitted genes found in paper text"

    signals = score_paper_signals(paper, user_genes, analysis_input)
    best = best_paper_signal(signals)
    best_score = best.assignment_score if best else 0.0
    best_type = best.evidence_type if best else "gene_mention_only"

    scrna_boost = 0.0
    if best and best.scrna_phrase_hits:
        scrna_boost = 0.15
    recency_boost = 0.12 if (paper.publication_year or 0) >= 2020 else 0.0
    fulltext_boost = 0.10 if paper.full_text else 0.0

    # Weight assignment language heavily; gene count lightly
    score = (
        best_score * 5.0
        + min(gene_count, 6) * 0.25
        + scrna_boost
        + recency_boost
        + fulltext_boost
    )

    if best and best.evidence_type in STRONG_EVIDENCE_TYPES:
        reason = (
            f"Ranked high: {len(best.detected_user_genes)} gene(s) near "
            f"'{best.annotation_phrase_hits[0] if best.annotation_phrase_hits else best.reason}' "
            f"({best.evidence_type.replace('_', ' ')})"
        )
    elif gene_count >= 3:
        reason = f"Moderate rank: {gene_count} genes mentioned; weak assignment language"
    else:
        reason = f"Lower rank: {gene_count} gene(s); no strong marker-to-celltype assignment"

    return score, reason


def top_scored_chunks(
    signals: list[EvidenceSignal],
    *,
    min_score: float = 0.4,
    limit: int = 3,
) -> list[EvidenceSignal]:
    """Return top chunks suitable for extraction or LLM prompts."""
    eligible = [
        s for s in signals
        if s.assignment_score >= min_score and s.evidence_type in EMITTABLE_EVIDENCE_TYPES
    ]
    if not eligible:
        eligible = [s for s in signals if s.evidence_type in EMITTABLE_EVIDENCE_TYPES]
    return eligible[:limit]


def match_strength_from_signal(
    evidence_type: EvidenceType,
    matched_genes: list[str],
    *,
    is_broad_label: bool = False,
    section: EvidenceSection = "abstract",
) -> MatchStrength:
    """Map evidence type + context to High/Medium/Low match strength."""
    n = len(matched_genes)
    if evidence_type == "direct_marker_celltype_assignment":
        if n >= 2 and not is_broad_label:
            return "High"
        if n >= 1 and section in ("results", "figure", "table"):
            return "High" if n >= 2 else "Medium"
        return "Medium" if n >= 2 else "Low"

    if evidence_type == "cluster_annotation":
        if n >= 2:
            return "High" if not is_broad_label else "Medium"
        return "Medium" if section != "abstract" else "Low"

    if evidence_type == "differential_expression_only":
        return "Low"

    if n >= 2:
        return "Medium"
    return "Low"


def is_broad_label_text(label: str) -> bool:
    norm = label.strip().lower()
    broad = {
        "epithelial", "epithelial cell", "epithelial cells",
        "immune", "immune cell", "immune cells",
        "stromal", "stromal cell", "fibroblast", "fibroblasts",
        "myeloid", "endothelial", "endothelial cell",
    }
    return norm in broad


def infer_evidence_type_for_window(
    window_text: str,
    user_genes: list[str],
    matched_genes: list[str],
    analysis_input: AnalysisInput | None = None,
) -> EvidenceType:
    """
    Classify a multi-sentence window; upgrade to assignment when genes + phrases co-occur.
    """
    lower = window_text.lower()
    sig = score_evidence_chunk(window_text, user_genes, analysis_input)
    if len(matched_genes) >= 2 and sig.annotation_phrase_hits:
        if any(p in lower for p in ("cluster", "subcluster")) and "annotated" in lower:
            return "cluster_annotation"
        return "direct_marker_celltype_assignment"
    if len(matched_genes) >= 2 and any(p in lower for p in ASSIGNMENT_PHRASES):
        return "direct_marker_celltype_assignment"
    if sig.evidence_type in EMITTABLE_EVIDENCE_TYPES:
        return sig.evidence_type
    if len(matched_genes) >= 2:
        return "cluster_annotation"
    return sig.evidence_type


def evidence_type_sort_key(evidence_type: str) -> int:
    order = {
        "direct_marker_celltype_assignment": 5,
        "cluster_annotation": 4,
        "differential_expression_only": 2,
        "gene_mention_only": 1,
        "celltype_mention_only": 0,
        "unrelated": 0,
    }
    return order.get(evidence_type, 0)
