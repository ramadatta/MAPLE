"""Parse user input to detect marker genes and optional context."""
from __future__ import annotations

import re
from typing import Optional

from maple.models import AnalysisInput

# Attempt to import the existing parsers; fall back gracefully if unavailable.
try:
    from utils.gene_parser import parse_genes as _parse_genes
    _HAS_GENE_PARSER = True
except ImportError:
    _HAS_GENE_PARSER = False

try:
    from utils.bulk_input_parser import parse_annotation_input as _parse_annotation_input
    _HAS_BULK_PARSER = True
except ImportError:
    _HAS_BULK_PARSER = False

# Regex patterns to extract context hints from free-form text
_TISSUE_RE = re.compile(
    r"(?:tissue|organ|from)\s*[:\-]?\s*([A-Za-z][\w\s]{1,30}?)(?:[,.\n]|$)",
    re.IGNORECASE,
)
_DISEASE_RE = re.compile(
    r"(?:disease|condition|disorder|diagnosis)\s*[:\-]?\s*([A-Za-z][\w\s]{1,40}?)(?:[,.\n]|$)",
    re.IGNORECASE,
)
_SPECIES_RE = re.compile(
    r"(?:species|organism)\s*[:\-]?\s*(human|mouse|rat|Homo sapiens|Mus musculus)(?:[,.\s]|$)",
    re.IGNORECASE,
)
_TECHNOLOGY_RE = re.compile(
    r"(?:technology|platform|method|assay)\s*[:\-]?\s*([A-Za-z0-9][\w\s\-]{1,30}?)(?:[,.\n]|$)",
    re.IGNORECASE,
)
_STRUCTURED_LABEL_RE = re.compile(
    r"^\s*(?:(?:marker\s+)?genes?|markers?|tissue|disease|species|technology)\s*:",
    re.IGNORECASE | re.MULTILINE,
)

# English/context tokens that look like gene symbols but are not
_CONTEXT_STOPWORDS = frozenset({
    "TISSUE", "DISEASE", "SPECIES", "MARKERS", "MARKER", "GENES", "GENE",
    "HUMAN", "MOUSE", "RAT", "LUNG", "LIVER", "BRAIN", "KIDNEY", "HEART",
    "IDIOPATHIC", "PULMONARY", "FIBROSIS", "CANCER", "CONTEXT", "OTHER",
    "FIND", "PAPERS", "WHERE", "THESE", "DEFINE", "CELL", "TYPE", "TYPES",
    "FROM", "WITH", "AND", "THE", "FOR", "IPF", "RNA", "SEQ", "SCRNA",
})


def _user_input_to_analysis_input(ui) -> AnalysisInput:
    """Convert an existing UserInput schema object to AnalysisInput."""
    return AnalysisInput(
        markers=ui.genes,
        tissue=ui.tissue or None,
        disease=ui.disease or None,
        species=ui.species if ui.species and ui.species != "Human" else None,
    )


def parse_user_message(text: str) -> Optional[AnalysisInput]:
    """
    Detect markers and optional context from a free-form message.

    Strategy:
    1. Try the existing bulk input parser (handles labeled blocks and gene lists).
    2. If that fails, fall back to simple gene-symbol detection in free-form text.

    Returns None if no marker genes can be detected.
    """
    if not text or not text.strip():
        return None

    stripped = text.strip()
    has_structured_labels = bool(_STRUCTURED_LABEL_RE.search(stripped))

    # --- Step 1: try existing structured parser ---
    if _HAS_BULK_PARSER:
        try:
            user_input = _parse_annotation_input(stripped)
            if user_input is not None and user_input.genes:
                return _user_input_to_analysis_input(user_input)
        except Exception:
            pass

    # Structured labels present but bulk parser failed — parse genes from non-label lines only.
    if has_structured_labels:
        non_label_lines = [
            line for line in stripped.splitlines()
            if line.strip() and not _STRUCTURED_LABEL_RE.match(line)
        ]
        if non_label_lines:
            gene_text = "\n".join(non_label_lines)
            if _HAS_GENE_PARSER:
                genes, _warnings = _parse_genes(gene_text)
            else:
                genes = []
                seen_nl: set[str] = set()
                for token in re.split(r"[\s,;|/\t\n]+", gene_text):
                    clean = token.strip("()[]{}\"'.:!?").upper()
                    if (
                        clean
                        and clean not in _CONTEXT_STOPWORDS
                        and 2 <= len(clean) <= 10
                        and re.match(r"^[A-Z][A-Z0-9]*$", clean)
                        and clean not in seen_nl
                    ):
                        seen_nl.add(clean)
                        genes.append(clean)
            if len(genes) >= 2:
                tissue_m = _TISSUE_RE.search(stripped)
                disease_m = _DISEASE_RE.search(stripped)
                species_m = _SPECIES_RE.search(stripped)
                technology_m = _TECHNOLOGY_RE.search(stripped)
                return AnalysisInput(
                    markers=genes,
                    tissue=tissue_m.group(1).strip() if tissue_m else None,
                    disease=disease_m.group(1).strip() if disease_m else None,
                    species=species_m.group(1).strip() if species_m else None,
                    technology=technology_m.group(1).strip() if technology_m else None,
                )
        return None

    # --- Step 2: heuristic free-form detection ---
    # Look for sequences of UPPERCASE tokens that look like gene symbols.
    gene_candidates: list[str] = []
    seen: set[str] = set()

    for token in re.split(r"[\s,;|/\t\n]+", stripped):
        clean = token.strip("()[]{}\"'.:!?").upper()
        if not clean or clean in _CONTEXT_STOPWORDS:
            continue
        # Gene symbols: 2-10 chars, at least 1 uppercase letter, no digits only
        if (
            2 <= len(clean) <= 10
            and re.match(r"^[A-Z][A-Z0-9]*$", clean)
            and not clean.isdigit()
            and clean not in seen
        ):
            seen.add(clean)
            gene_candidates.append(clean)

    if _HAS_GENE_PARSER and gene_candidates:
        genes, _warnings = _parse_genes(" ".join(gene_candidates))
    else:
        genes = gene_candidates

    if not genes:
        return None

    # Need at least 2 gene-like tokens to avoid false positives on short commands
    if len(genes) < 2:
        return None

    # Extract optional context from text
    tissue: Optional[str] = None
    tissue_m = _TISSUE_RE.search(stripped)
    if tissue_m:
        tissue = tissue_m.group(1).strip() or None

    disease: Optional[str] = None
    disease_m = _DISEASE_RE.search(stripped)
    if disease_m:
        disease = disease_m.group(1).strip() or None

    species: Optional[str] = None
    species_m = _SPECIES_RE.search(stripped)
    if species_m:
        species = species_m.group(1).strip() or None

    technology: Optional[str] = None
    technology_m = _TECHNOLOGY_RE.search(stripped)
    if technology_m:
        technology = technology_m.group(1).strip() or None

    return AnalysisInput(
        markers=genes,
        tissue=tissue,
        disease=disease,
        species=species,
        technology=technology,
    )
