"""Gene symbol aliases for marker matching."""

from __future__ import annotations

# Input symbol -> canonical symbols counted against marker rules
GENE_ALIASES: dict[str, list[str]] = {
    "SFTPA": ["SFTPA1", "SFTPA2"],
    "PRX": ["PERIAXIN"],
    "ACKR1": ["DARC", "DUFFY ANTIGEN RECEPTOR FOR CHEMOKINES"],
    "PLVAP": ["PV1", "PLASMALEMMA VESICLE ASSOCIATED PROTEIN"],
    "VWA1": ["VON WILLEBRAND FACTOR A DOMAIN CONTAINING 1"],
}

AT2_MARKERS = {"SFTPC", "SFTPB", "SFTPA", "SFTPA1", "SFTPA2", "ABCA3", "LAMP3", "ETV5"}


def expand_gene_set(genes: list[str]) -> set[str]:
    """Expand user genes with aliases for marker-rule matching."""
    expanded: set[str] = set()
    for gene in genes:
        upper = gene.upper()
        expanded.add(upper)
        for alias_target in GENE_ALIASES.get(upper, []):
            expanded.add(alias_target.upper())
    return expanded


def cell_type_for_gene(gene: str, rules: dict[str, list[str]]) -> str | None:
    """Return canonical cell type if gene is a known marker (including aliases)."""
    upper = gene.upper()
    check = {upper}
    for canonical, aliases in GENE_ALIASES.items():
        if upper == canonical or upper in {a.upper() for a in aliases}:
            check.add(canonical.upper())
            check.update(a.upper() for a in aliases)

    for cell_type, markers in rules.items():
        marker_set = {m.upper() for m in markers}
        if check & marker_set:
            return cell_type
    return None


def pubmed_gene_query(gene: str) -> str:
    """Return a PubMed search expression for a gene symbol plus aliases.

    The canonical gene field stays first for precision, while alias terms are
    added in title/abstract to help with ambiguous symbols and papers that use
    the protein or common-name form instead of the symbol.
    """
    upper = gene.upper().strip()
    terms = [f'{upper}[Gene]']
    for alias in GENE_ALIASES.get(upper, []):
        alias_text = alias.strip()
        if not alias_text:
            continue
        terms.append(f'"{alias_text}"[Title/Abstract]')
    if len(terms) == 1:
        return terms[0]
    return "(" + " OR ".join(terms) + ")"
