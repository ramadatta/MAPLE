"""Finding a gene in paper text, including the other names papers use for it.

Matching ignores case, so mouse spellings ("Krt17") count as the human symbol
("KRT17"). On top of that, a small, conservative list covers:

* protein / common names papers use instead of the symbol ("p63" for TP63,
  "CK17" for KRT17, "CD45" for PTPRC);
* mouse orthologs whose symbol differs from the human one (HLA-DRA -> H2-Ea).

Only names that are unambiguous in cell-biology text are listed. Gene names that
appear in the paper are always reported back as the user's own symbols.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

# symbol -> other names used in papers (protein names, common names, mouse orthologs)
TEXT_ALIASES: dict[str, tuple[str, ...]] = {
    # transcription factors / cell-cycle
    "TP63": ("p63", "ΔNp63", "deltaNp63", "Trp63"),
    "TP53": ("p53", "Trp53"),
    "CDKN1A": ("p21", "p21Cip1", "p21Waf1", "p21WAF1/CIP1", "p21CIP1/WAF1"),
    "CDKN2A": ("p16", "p16INK4a", "p16Ink4a", "p16INK4A"),
    "MKI67": ("Ki67", "Ki-67"),
    # keratins
    "KRT5": ("CK5", "cytokeratin 5", "keratin 5"),
    "KRT8": ("CK8", "cytokeratin 8", "keratin 8"),
    "KRT14": ("CK14", "cytokeratin 14", "keratin 14"),
    "KRT17": ("CK17", "cytokeratin 17", "keratin 17"),
    "KRT18": ("CK18", "cytokeratin 18", "keratin 18"),
    "KRT19": ("CK19", "cytokeratin 19", "keratin 19"),
    # surface markers known by CD names
    "PTPRC": ("CD45",),
    "PECAM1": ("CD31",),
    "EPCAM": ("CD326",),
    "PROM1": ("CD133",),
    "THY1": ("CD90",),
    "ENG": ("CD105",),
    "NT5E": ("CD73",),
    "MS4A1": ("CD20",),
    "ITGAM": ("CD11b",),
    "ITGAX": ("CD11c",),
    "NCAM1": ("CD56",),
    "FCGR3A": ("CD16", "Fcgr4"),          # mouse FcγRIV is the FCGR3A ortholog
    "CEACAM6": ("CD66c",),
    "CD274": ("PD-L1",),
    "PDCD1": ("PD-1",),
    # other widely used protein names
    "ACTA2": ("α-SMA", "alpha-SMA", "αSMA", "alpha smooth muscle actin"),
    "SFTPC": ("SP-C", "proSP-C", "pro-SPC", "pro-SP-C"),
    "SFTPB": ("SP-B",),
    "SCGB1A1": ("CC10", "CCSP", "Scgb1a1"),
    "CDH1": ("E-cadherin",),
    "CDH2": ("N-cadherin",),
    "VIM": ("vimentin",),
    "POSTN": ("periostin",),
    # VEGF receptors and other genes better known by their receptor/protein names
    "KDR": ("VEGFR2", "VEGFR-2", "FLK1", "Flk-1"),
    "FLT4": ("VEGFR3", "VEGFR-3"),
    "FLT1": ("VEGFR1", "VEGFR-1"),
    "TEK": ("TIE2", "Tie-2"),
    "APLN": ("apelin",),
    "CXCR4": ("CD184",),
    # MHC class II: mouse H2 genes have different symbols
    "HLA-DRA": ("H2-Ea",),
    "HLA-DRB1": ("H2-Eb1",),
    "HLA-DQA1": ("H2-Aa",),
    "HLA-DQB1": ("H2-Ab1",),
}


def aliases(gene: str) -> tuple[str, ...]:
    """Other names for ``gene``. Works from the symbol or from any listed alias
    (VEGFR3 -> FLT4, VEGFR-3), so it does not matter which name the user typed."""
    sym = official_symbol(gene)
    names = (sym,) + TEXT_ALIASES.get(sym, ())
    return tuple(n for n in names if n.upper() != gene.upper())


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    return {a.upper(): sym for sym, names in TEXT_ALIASES.items() for a in names}


def official_symbol(gene: str) -> str:
    """The symbol a name belongs to: 'VEGFR3' -> 'FLT4'; unknown names come back upper-cased."""
    up = gene.strip().upper()
    if up in TEXT_ALIASES:
        return up
    return _alias_index().get(up, up)


@lru_cache(maxsize=4096)
def _pattern(gene: str) -> re.Pattern:
    names = [gene] + list(aliases(gene))
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # whole-token match: no letter/digit directly before or after
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alternatives})(?![A-Za-z0-9])", re.IGNORECASE)


def gene_in_text(gene: str, text: str) -> bool:
    return bool(text) and _pattern(gene.upper()).search(text) is not None


def genes_in_text(genes: Iterable[str], text: str) -> list[str]:
    """The user's genes (as given) that appear in ``text`` under any listed name."""
    if not text:
        return []
    return [g for g in genes if gene_in_text(g, text)]


def canonical_symbol(name: str, genes: Iterable[str]) -> str | None:
    """Map a name returned by the model ("p63", "Krt17") back to the user's symbol."""
    upper = name.strip().upper()
    for g in genes:
        if upper == g.upper() or upper in {a.upper() for a in aliases(g)}:
            return g
    return None


def describe_for_prompt(genes: Iterable[str]) -> str:
    """'TP63 (may appear as p63, ΔNp63), KRT17 (may appear as CK17, …)'."""
    parts = []
    for g in genes:
        alt = [a for a in aliases(g) if a.upper() != g.upper()]
        parts.append(f"{g} (may appear as {', '.join(alt)})" if alt else g)
    return ", ".join(parts)
