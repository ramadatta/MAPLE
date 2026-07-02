"""Parse structured or single-shot annotation input from pasted text."""

from __future__ import annotations

import re
from typing import Optional

from models.schemas import SpeciesOption, UserInput
from utils.gene_parser import parse_genes

_GENE_LABEL = re.compile(
    r"^\s*(?:(?:marker\s+)?genes?|markers?)\s*:\s*(.*)$",
    re.IGNORECASE,
)
_TISSUE_LABEL = re.compile(r"^\s*tissue\s*:\s*(.*)$", re.IGNORECASE)
_DISEASE_LABEL = re.compile(r"^\s*disease\s*:\s*(.*)$", re.IGNORECASE)
_SPECIES_LABEL = re.compile(r"^\s*species\s*:\s*(.*)$", re.IGNORECASE)
_PAPERS_LABEL = re.compile(r"^\s*papers(?:\s*per\s*query)?\s*:\s*(.*)$", re.IGNORECASE)
_ANY_LABEL = re.compile(
    r"^\s*(?:(?:marker\s+)?genes?|markers?|tissue|disease|species|papers(?:\s*per\s*query)?)\s*:",
    re.IGNORECASE,
)

_DEFAULT_TISSUE = ""
_DEFAULT_DISEASE = ""
_DEFAULT_SPECIES: SpeciesOption = "Human"


def _normalize_species(value: str) -> SpeciesOption:
    lower = value.strip().lower()
    if "mouse" in lower or lower == "mus musculus":
        return "Mouse"
    if "human" in lower or lower == "homo sapiens":
        return "Human"
    return "Other"


def _parse_labeled_block(text: str) -> dict[str, str]:
    """Extract labeled fields from multi-line pasted input."""
    fields: dict[str, str] = {}
    current_key: Optional[str] = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current_key, buffer
        if current_key is not None:
            fields[current_key] = "\n".join(buffer).strip()
        buffer = []

    for line in text.splitlines():
        for key, pattern in (
            ("genes", _GENE_LABEL),
            ("tissue", _TISSUE_LABEL),
            ("disease", _DISEASE_LABEL),
            ("species", _SPECIES_LABEL),
            ("papers", _PAPERS_LABEL),
        ):
            match = pattern.match(line)
            if match:
                flush()
                current_key = key
                inline = match.group(1).strip()
                buffer = [inline] if inline else []
                break
        else:
            if current_key is not None:
                buffer.append(line)

    flush()
    return fields


def parse_annotation_input(raw: str) -> Optional[UserInput]:
    """
    Parse pasted annotation input into UserInput.

    Supports:
    - Full labeled block (Marker genes / Tissue / Disease / Species)
    - Plain comma/newline-separated gene list (uses defaults for context)

    Returns None if the text does not look like annotation input.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()
    lower = text.lower()

    if lower in ("start", "annotate", "run", "begin"):
        return None

    fields = _parse_labeled_block(text)
    gene_raw = fields.get("genes", "")

    if not gene_raw and _ANY_LABEL.search(text):
        gene_raw = fields.get("genes", "")

    if not gene_raw:
        if _ANY_LABEL.search(text):
            return None
        gene_raw = text

    genes, warnings = parse_genes(gene_raw)
    if not genes:
        return None

    has_labels = bool(fields)
    looks_like_gene_list_only = not has_labels and (
        "," in gene_raw or "\n" in gene_raw or len(genes) >= 3
    )
    has_gene_label = "genes" in fields or bool(
        re.search(r"^\s*(?:(?:marker\s+)?genes?|markers?)\s*:", text, re.IGNORECASE | re.MULTILINE)
    )

    if not has_gene_label and not looks_like_gene_list_only:
        return None

    tissue = (fields.get("tissue") or "").strip()
    disease = (fields.get("disease") or "").strip()
    species: SpeciesOption = (
        _normalize_species(fields["species"]) if fields.get("species") else _DEFAULT_SPECIES
    )

    papers_per_query = 5
    if fields.get("papers"):
        try:
            papers_per_query = max(1, min(20, int(fields["papers"].strip())))
        except ValueError:
            pass

    return UserInput(
        genes=genes,
        tissue=tissue,
        disease=disease,
        species=species,
        papers_per_query=papers_per_query,
        parse_warnings=warnings,
    )
