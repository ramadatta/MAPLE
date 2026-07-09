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
    "HUMAN", "MOUSE", "RAT", "LUNG", "LUNGS", "LIVER", "BRAIN", "KIDNEY", "HEART",
    "IDIOPATHIC", "PULMONARY", "FIBROSIS", "CANCER", "CONTEXT", "OTHER",
    "FIND", "PAPERS", "PAPER", "WHERE", "THESE", "DEFINE", "DEFINES", "DEFINED",
    "CELL", "CELLS", "TYPE", "TYPES", "SUBTYPE", "SUBTYPES", "POPULATION",
    "SUBPOPULATION", "STATE", "CLUSTER", "SIGNATURE", "LITERATURE",
    "FROM", "WITH", "AND", "THE", "FOR", "IPF", "RNA", "SEQ", "SCRNA",
    # common instruction / filler words that appear in free-form context requests
    "FOCUS", "FOCUSING", "ONLY", "ON", "IN", "OF", "TO", "BY", "AS", "AT",
    "OR", "IF", "IS", "ARE", "BE", "ALSO", "JUST", "LIKE", "LOOK", "LOOKING",
    "SEARCH", "SHOW", "WANT", "PLEASE", "ANNOTATE", "ANNOTATED", "ANNOTATION",
    "DESCRIBE", "DESCRIBED", "PUBLISHED", "STUDIES", "STUDY", "DATA", "RESTRICT",
    "RESTRICTED", "LIMIT", "LIMITED", "SPECIFIC", "SPECIFICALLY", "CONDITION",
})


# ── Free-text context lexicons ────────────────────────────────────────────────
# Species — surface form -> canonical.
_SPECIES_LEX: dict[str, str] = {
    "human": "human", "homo sapiens": "human", "patient": "human",
    "mouse": "mouse", "mus musculus": "mouse", "murine": "mouse",
    "rat": "rat", "rattus": "rat", "zebrafish": "zebrafish", "danio": "zebrafish",
    "macaque": "macaque", "monkey": "macaque", "pig": "pig", "porcine": "pig",
    "drosophila": "drosophila", "fly": "drosophila",
}

# Tissues / organs (singular canonical). Plurals handled by trailing-'s' match.
_TISSUE_LEX: dict[str, str] = {t: t for t in [
    "lung", "liver", "brain", "kidney", "heart", "skin", "blood", "bone marrow",
    "pancreas", "intestine", "colon", "stomach", "gut", "breast", "prostate",
    "muscle", "skeletal muscle", "adipose", "spleen", "thymus", "lymph node",
    "retina", "eye", "placenta", "ovary", "testis", "bladder", "spinal cord",
    "esophagus", "trachea", "airway", "tonsil", "pleura", "synovium", "cartilage",
    "endometrium", "cervix", "thyroid", "adrenal", "salivary gland", "cornea",
]}

# Disease abbreviations -> canonical full term (better for PubMed retrieval).
_DISEASE_ABBR: dict[str, str] = {
    "ipf": "idiopathic pulmonary fibrosis",
    "copd": "chronic obstructive pulmonary disease",
    "covid": "COVID-19", "covid-19": "COVID-19",
    "als": "amyotrophic lateral sclerosis",
    "nsclc": "non-small cell lung cancer",
    "sclc": "small cell lung cancer",
    "hcc": "hepatocellular carcinoma",
    "ibd": "inflammatory bowel disease",
    "ra": "rheumatoid arthritis",
    "aml": "acute myeloid leukemia",
    "cml": "chronic myeloid leukemia",
    "pdac": "pancreatic ductal adenocarcinoma",
    "ccrcc": "clear cell renal cell carcinoma",
    "ad": "Alzheimer's disease",
    "t2d": "type 2 diabetes", "t1d": "type 1 diabetes",
    "nash": "non-alcoholic steatohepatitis",
}

# Word endings that mark a disease phrase in free text.
_DISEASE_SUFFIX = (
    "fibrosis", "cancer", "carcinoma", "adenocarcinoma", "tumor", "tumour",
    "sarcoma", "leukemia", "leukaemia", "lymphoma", "melanoma", "glioma",
    "sclerosis", "syndrome", "disease", "cardiomyopathy", "nephropathy",
    "steatohepatitis", "colitis", "dermatitis", "arthritis", "hepatitis",
)
_DISEASE_PHRASE_RE = re.compile(
    r"\b((?:[A-Za-z][\w'-]+\s+){0,3}[A-Za-z][\w'-]*(?:"
    + "|".join(_DISEASE_SUFFIX)
    + r"))\b",
    re.IGNORECASE,
)


def _match_lexicon(text_lower: str, lex: dict[str, str]) -> tuple[str, list[str]]:
    """Return (canonical, matched_surface_words) for the first lexicon hit."""
    # Longest surface forms first so "bone marrow" beats "bone".
    for surface in sorted(lex, key=len, reverse=True):
        pattern = r"\b" + re.escape(surface) + r"s?\b"
        if re.search(pattern, text_lower):
            return lex[surface], surface.split()
    return "", []


def _extract_context(text: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], set[str]]:
    """
    Pull tissue / disease / species / technology from free-form text.

    Returns (tissue, disease, species, technology, tokens_to_exclude) where the
    last item is the set of UPPERCASE word tokens that were consumed as context
    (so they are not misread as gene symbols).
    """
    text_lower = text.lower()
    exclude: set[str] = set()

    tissue = disease = species = technology = None

    # Labeled forms win (explicit "tissue: ...").
    m = _TISSUE_RE.search(text)
    if m:
        tissue = m.group(1).strip() or None
    m = _DISEASE_RE.search(text)
    if m:
        disease = m.group(1).strip() or None
    m = _SPECIES_RE.search(text)
    if m:
        species = m.group(1).strip() or None
    m = _TECHNOLOGY_RE.search(text)
    if m:
        technology = m.group(1).strip() or None

    # Lexicon fallback for natural-language phrasing.
    if not species:
        sp, words = _match_lexicon(text_lower, _SPECIES_LEX)
        if sp:
            species = sp
            exclude.update(w.upper() for w in words)
    if not tissue:
        ti, words = _match_lexicon(text_lower, _TISSUE_LEX)
        if ti:
            tissue = ti
            exclude.update(w.upper() for w in words)
            exclude.add((ti + "s").upper())

    if not disease:
        # Disease abbreviations (word-boundary, case-insensitive).
        for abbr, full in _DISEASE_ABBR.items():
            if re.search(r"\b" + re.escape(abbr) + r"\b", text_lower):
                disease = full
                exclude.add(abbr.upper())
                break
    if not disease:
        m = _DISEASE_PHRASE_RE.search(text)
        if m:
            phrase = m.group(1).strip()
            disease = phrase
            exclude.update(w.upper() for w in re.split(r"\s+", phrase))

    return tissue, disease, species, technology, exclude


def _user_input_to_analysis_input(ui) -> AnalysisInput:
    """Convert an existing UserInput schema object to AnalysisInput."""
    return AnalysisInput(
        markers=ui.genes,
        tissue=ui.tissue or None,
        disease=ui.disease or None,
        species=ui.species if ui.species and ui.species != "Human" else None,
    )


def _detect_gene_tokens(text: str, exclude: set[str]) -> list[str]:
    """Pull gene-symbol-like tokens from free-form text, skipping stopwords."""
    gene_candidates: list[str] = []
    seen: set[str] = set()
    # Normalise a leading R-style c( wrapper so the first symbol isn't fused to "c".
    text = re.sub(r"^\s*c\s*\(", "(", text, flags=re.IGNORECASE)
    for token in re.split(r"[\s,;|/\t\n]+", text):
        clean = token.strip("()[]{}\"'.:!?`").upper()
        if not clean or clean in _CONTEXT_STOPWORDS or clean in exclude:
            continue
        # Gene symbols: 2-10 chars, letters/digits, at least one letter, not all digits.
        if (
            2 <= len(clean) <= 10
            and re.match(r"^[A-Z][A-Z0-9]*$", clean)
            and not clean.isdigit()
            and clean not in seen
        ):
            seen.add(clean)
            gene_candidates.append(clean)
    return gene_candidates


def parse_user_message(text: str) -> Optional[AnalysisInput]:
    """
    Detect markers and optional context from a free-form message.

    Accepts genes as comma / tab / newline / semicolon / pipe separated lists,
    Python or R list syntax, or labeled blocks (``Markers:`` / ``Tissue:`` …).
    Free-text context ("focus on lung, IPF") is parsed and used to focus retrieval.

    Returns None if no marker genes can be detected.
    """
    if not text or not text.strip():
        return None

    stripped = text.strip()
    has_structured_labels = bool(_STRUCTURED_LABEL_RE.search(stripped))

    # ── Labeled input (Markers:/Tissue:/Disease: …) → structured parser ──────────
    if has_structured_labels:
        if _HAS_BULK_PARSER:
            try:
                user_input = _parse_annotation_input(stripped)
                if user_input is not None and user_input.genes:
                    return _user_input_to_analysis_input(user_input)
            except Exception:
                pass

        # Bulk parser failed — parse genes from the non-label lines only.
        non_label_lines = [
            line for line in stripped.splitlines()
            if line.strip() and not _STRUCTURED_LABEL_RE.match(line)
        ]
        if non_label_lines:
            gene_text = "\n".join(non_label_lines)
            if _HAS_GENE_PARSER:
                genes, _warnings = _parse_genes(gene_text)
            else:
                genes = _detect_gene_tokens(gene_text, set())
            if len(genes) >= 2:
                tissue, disease, species, technology, _ = _extract_context(stripped)
                return AnalysisInput(
                    markers=genes,
                    tissue=tissue,
                    disease=disease,
                    species=species,
                    technology=technology,
                )
        return None

    # ── Free-form input: extract context first, then genes (minus context) ───────
    tissue, disease, species, technology, ctx_exclude = _extract_context(stripped)

    gene_candidates = _detect_gene_tokens(stripped, ctx_exclude)

    if _HAS_GENE_PARSER and gene_candidates:
        genes, _warnings = _parse_genes(" ".join(gene_candidates))
    else:
        genes = gene_candidates

    # Need at least 2 gene-like tokens to avoid false positives on short commands.
    if len(genes) < 2:
        return None

    return AnalysisInput(
        markers=genes,
        tissue=tissue,
        disease=disease,
        species=species,
        technology=technology,
    )
