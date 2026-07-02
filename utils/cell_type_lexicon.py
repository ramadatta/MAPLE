"""Map literature phrases in PubMed text to canonical cell types.

Covers lung, liver, brain/CNS, kidney, heart, intestine, pancreas, and
pan-tissue immune populations. Canonical names are tissue-specific where
possible (e.g. "Kupffer cell" not just "Macrophage") to give accurate
annotations beyond lineage-level calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_MARKER_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "marker_rules.json"

# Canonical names that are too generic to show without a more-specific phrase.
# When one of these is matched but a longer phrase from the paper is available,
# the raw phrase is used as the display name instead.
_GENERIC_CANONICAL: frozenset[str] = frozenset({
    "Macrophage",
    "T cell",
    "B cell",
    "Endothelial",
    "Epithelial",
    "Fibroblast",
    "Immune",
    "Lymphocyte",
    "Monocyte",
    "Dendritic cell",
    "Neutrophil",
})

# Synonyms used in papers but absent from canonical marker-rule names.
# Longer / more specific phrases first within each group so phrase-length
# tie-breaking returns the most informative match.
LITERATURE_SYNONYMS: dict[str, list[str]] = {

    # ── LUNG ──────────────────────────────────────────────────────────────────
    "Alveolar type 2 epithelial": [
        "alveolar type ii epithelial",
        "alveolar type 2 epithelial",
        "alveolar epithelial type ii",
        "alveolar epithelial type 2",
        "type ii alveolar epithelial",
        "type 2 alveolar epithelial",
        "type ii pneumocyte",
        "type 2 pneumocyte",
        "type ii alveolar",
        "type 2 alveolar",
        "at2 epithelial",
        "at2 cell",
        "at2 cells",
        "at ii",
        "surfactant-producing cell",
        "surfactant producing cell",
        "surfactant-producing",
        "surfactant producing",
    ],
    "Alveolar type 1 epithelial": [
        "alveolar type i epithelial",
        "alveolar type 1 epithelial",
        "type i pneumocyte",
        "type 1 pneumocyte",
        "at1 cell",
        "at1 cells",
        "at i",
        "squamous alveolar",
    ],
    "Alveolar type 0 epithelial": [
        "type-0 alveolar epithelial cell",
        "type-0 alveolar epithelial cells",
        "type 0 alveolar epithelial cell",
        "type 0 alveolar epithelial cells",
        "alveolar type-0",
        "alveolar type 0",
        "at0 cell",
        "at0 cells",
        "at0s",
        "at0",
    ],
    "TRB-secretory cell": [
        "trb-secretory cell",
        "trb-secretory cells",
        "trb-secretory",
        "trb-sc",
        "trb-scs",
        "terminal respiratory bronchiole secretory cell",
        "terminal respiratory bronchiole secretory cells",
    ],
    "SCGB3A2-ciliated cell": [
        "scgb3a2-ciliated cell",
        "scgb3a2-ciliated cells",
        "scgb3a2-cc",
        "scgb3a2-ccs",
    ],
    "Club epithelial": [
        "club cell",
        "club cells",
        "clara cell",
        "clara cells",
        "bronchiolar secretory",
    ],
    "Basal epithelial": [
        "basal epithelial cell",
        "basal epithelial cells",
        "basal cell",
        "basal cells",
    ],
    "Aberrant basaloid": [
        "aberrant basaloid cell",
        "aberrant basaloid cells",
        "aberrant basaloid",
        "krt17+ basaloid",
        "krt5-/krt17+",
        "basaloid cell",
        "basaloid cells",
    ],
    "Myofibroblast": [
        "myofibroblasts",
        "myofibroblast",
        "contractile fibroblast",
        "acta2-positive fibroblast",
        "acta2 positive fibroblast",
        "alpha-sma positive fibroblast",
        "alpha-sma",
        "α-sma",
        "α sma",
    ],
    "Activated fibroblast": [
        "disease-associated fibroblast",
        "pathogenic fibroblast",
        "inflammatory fibroblast",
        "activated fibroblast",
        "activated fibroblasts",
    ],
    "Fibroblast": [
        "lung fibroblast",
        "pulmonary fibroblast",
        "interstitial fibroblast",
        "fibroblasts",
        "fibroblast",
    ],
    "Lymphatic endothelial": [
        "lymphatic endothelial cell",
        "lymphatic endothelial cells",
        "lymph vessel endothelial",
        "lymphatic vessel",
    ],

    # ── GENERAL VASCULAR ──────────────────────────────────────────────────────
    "Tip endothelial cell": [
        "tip endothelial cell",
        "tip endothelial cells",
        "tip cell",
        "sprouting endothelial",
        "angiogenic tip",
    ],
    "Arterial endothelial cell": [
        "arterial endothelial cell",
        "arterial endothelial",
        "artery endothelial",
    ],
    "Venous endothelial cell": [
        "venous endothelial cell",
        "venous endothelial",
        "vein endothelial",
    ],
    "Ectopic endothelial cell": [
        "ectopic endothelial cell state",
        "ectopic endothelial cell",
        "ectopic endothelial cells",
        "ectopic endothelial",
        "ectopic ecs",
        "ectopic ec",
    ],
    "Endothelial": [
        "vascular endothelial cell",
        "vascular endothelial cells",
        "endothelial cell",
        "endothelial cells",
        "endothelial cell state",
        "endothelial cells state",
    ],
    "Pericyte": [
        "pericyte",
        "pericytes",
        "mural cell",
        "microvascular pericyte",
    ],
    "Smooth muscle": [
        "smooth muscle cell",
        "smooth muscle cells",
        "vascular smooth muscle",
    ],

    # ── LIVER ─────────────────────────────────────────────────────────────────
    "Hepatocyte": [
        "hepatocyte",
        "hepatocytes",
        "liver parenchymal cell",
        "hepatic parenchymal cell",
        "pericentral hepatocyte",
        "periportal hepatocyte",
        "zone 1 hepatocyte",
        "zone 3 hepatocyte",
    ],
    "Hepatic stellate cell": [
        "hepatic stellate cell",
        "hepatic stellate cells",
        "activated hepatic stellate",
        "stellate cell",
        "stellate cells",
        "ito cell",
        "ito cells",
        "vitamin a-storing cell",
        "liver stellate",
    ],
    "Kupffer cell": [
        "kupffer cell",
        "kupffer cells",
        "liver-resident macrophage",
        "liver resident macrophage",
        "hepatic macrophage",
        "hepatic macrophages",
        "liver macrophage",
        "liver macrophages",
    ],
    "Cholangiocyte": [
        "cholangiocyte",
        "cholangiocytes",
        "bile duct cell",
        "bile duct cells",
        "biliary epithelial cell",
        "biliary epithelial cells",
        "intrahepatic biliary",
        "ductal plate cell",
    ],
    "Liver sinusoidal endothelial cell": [
        "liver sinusoidal endothelial cell",
        "liver sinusoidal endothelial cells",
        "hepatic sinusoidal endothelial",
        "sinusoidal endothelial cell",
        "sinusoidal endothelial",
        "lsec",
    ],
    "Portal fibroblast": [
        "portal fibroblast",
        "portal fibroblasts",
        "hepatic portal fibroblast",
    ],

    # ── BRAIN / CNS ───────────────────────────────────────────────────────────
    "Neuron": [
        "neuron",
        "neurons",
        "nerve cell",
        "nerve cells",
        "excitatory neuron",
        "inhibitory neuron",
        "pyramidal neuron",
        "cortical neuron",
        "hippocampal neuron",
        "dopaminergic neuron",
        "glutamatergic neuron",
        "gabaergic neuron",
        "cholinergic neuron",
    ],
    "Astrocyte": [
        "astrocyte",
        "astrocytes",
        "reactive astrocyte",
        "reactive astrocytes",
        "fibrous astrocyte",
        "protoplasmic astrocyte",
    ],
    "Oligodendrocyte": [
        "oligodendrocyte",
        "oligodendrocytes",
        "myelinating oligodendrocyte",
        "mature oligodendrocyte",
    ],
    "Oligodendrocyte precursor cell": [
        "oligodendrocyte precursor cell",
        "oligodendrocyte precursor cells",
        "opc",
        "opcs",
        "ng2+ cell",
        "ng2-positive cell",
        "polydendrocyte",
    ],
    "Microglia": [
        "microglia",
        "microglial cell",
        "microglial cells",
        "brain-resident macrophage",
        "brain resident macrophage",
        "homeostatic microglia",
        "activated microglia",
        "disease-associated microglia",
        "dam",
    ],
    "Radial glia": [
        "radial glia",
        "radial glial cell",
        "radial glial cells",
        "neural progenitor",
        "neural progenitors",
    ],
    "Ependymal cell": [
        "ependymal cell",
        "ependymal cells",
        "ependymocyte",
    ],

    # ── KIDNEY ────────────────────────────────────────────────────────────────
    "Proximal tubule cell": [
        "proximal tubule cell",
        "proximal tubule cells",
        "proximal tubular cell",
        "proximal tubular cells",
        "proximal convoluted tubule",
        "pct cell",
        "pt cell",
        "s1 tubule",
        "s2 tubule",
        "s3 tubule",
    ],
    "Podocyte": [
        "podocyte",
        "podocytes",
        "glomerular epithelial cell",
        "glomerular visceral epithelial",
        "foot process cell",
    ],
    "Distal tubule cell": [
        "distal tubule cell",
        "distal tubular cell",
        "distal convoluted tubule",
        "dct cell",
    ],
    "Collecting duct cell": [
        "collecting duct cell",
        "collecting duct cells",
        "principal cell",
        "intercalated cell",
        "alpha-intercalated cell",
        "beta-intercalated cell",
    ],
    "Mesangial cell": [
        "mesangial cell",
        "mesangial cells",
        "glomerular mesangial",
    ],
    "Loop of Henle cell": [
        "loop of henle",
        "thick ascending limb",
        "thin descending limb",
        "tal cell",
    ],
    "Kidney fibroblast": [
        "renal fibroblast",
        "renal fibroblasts",
        "kidney fibroblast",
        "interstitial fibroblast",
        "renal interstitial fibroblast",
    ],

    # ── HEART ─────────────────────────────────────────────────────────────────
    "Cardiomyocyte": [
        "cardiomyocyte",
        "cardiomyocytes",
        "cardiac myocyte",
        "cardiac myocytes",
        "heart muscle cell",
        "ventricular cardiomyocyte",
        "atrial cardiomyocyte",
    ],
    "Cardiac fibroblast": [
        "cardiac fibroblast",
        "cardiac fibroblasts",
        "myocardial fibroblast",
        "heart fibroblast",
        "activated cardiac fibroblast",
        "cardiac myofibroblast",
    ],
    "Sinoatrial node cell": [
        "sinoatrial node cell",
        "sa node cell",
        "pacemaker cell",
    ],

    # ── INTESTINE ─────────────────────────────────────────────────────────────
    "Enterocyte": [
        "enterocyte",
        "enterocytes",
        "intestinal epithelial cell",
        "intestinal epithelial cells",
        "absorptive enterocyte",
        "absorptive cell",
        "small intestinal enterocyte",
        "colonocyte",
    ],
    "Goblet cell": [
        "goblet cell",
        "goblet cells",
        "intestinal goblet",
        "colonic goblet",
        "mucus-secreting cell",
        "mucus secreting cell",
    ],
    "Paneth cell": [
        "paneth cell",
        "paneth cells",
        "intestinal paneth",
        "antimicrobial secretory cell",
    ],
    "Enteroendocrine cell": [
        "enteroendocrine cell",
        "enteroendocrine cells",
        "neuroendocrine cell",
        "chromaffin-like cell",
        "intestinal neuroendocrine",
    ],
    "Intestinal stem cell": [
        "intestinal stem cell",
        "intestinal stem cells",
        "crypt base columnar",
        "cbc cell",
        "lgr5-positive cell",
        "lgr5+ cell",
    ],
    "Tuft cell": [
        "tuft cell",
        "tuft cells",
        "brush cell",
        "chemosensory cell",
    ],

    # ── PANCREAS ──────────────────────────────────────────────────────────────
    "Pancreatic beta cell": [
        "pancreatic beta cell",
        "pancreatic beta cells",
        "beta cell",
        "β cell",
        "beta-cell",
        "insulin-secreting cell",
        "insulin secreting cell",
        "islet beta cell",
        "b cell islet",
    ],
    "Pancreatic alpha cell": [
        "pancreatic alpha cell",
        "pancreatic alpha cells",
        "alpha cell",
        "α cell",
        "alpha-cell",
        "glucagon-secreting cell",
        "glucagon secreting cell",
        "islet alpha cell",
    ],
    "Pancreatic delta cell": [
        "pancreatic delta cell",
        "delta cell",
        "somatostatin-secreting cell",
    ],
    "Pancreatic acinar cell": [
        "pancreatic acinar cell",
        "pancreatic acinar cells",
        "acinar cell",
        "acinar cells",
        "exocrine pancreatic cell",
    ],
    "Pancreatic ductal cell": [
        "pancreatic ductal cell",
        "pancreatic ductal cells",
        "ductal cell",
        "pancreatic duct cell",
    ],
    "Pancreatic stellate cell": [
        "pancreatic stellate cell",
        "pancreatic stellate cells",
        "activated pancreatic stellate",
    ],

    # ── BROAD IMMUNE (pan-tissue) ─────────────────────────────────────────────
    "Macrophage": [
        "tumor-associated macrophage",
        "tissue-resident macrophage",
        "interstitial macrophage",
        "alveolar macrophage",
        "m1 macrophage",
        "m2 macrophage",
        "classically activated macrophage",
        "alternatively activated macrophage",
        "trem2+ macrophage",
        "lipid-associated macrophage",
        "macrophages",
        "macrophage",
    ],
    "Dendritic cell": [
        "plasmacytoid dendritic cell",
        "plasmacytoid dc",
        "conventional dendritic cell",
        "conventional dc",
        "myeloid dendritic cell",
        "dendritic cells",
        "dendritic cell",
        "cdc1",
        "cdc2",
        "pdc",
    ],
    "Monocyte": [
        "classical monocyte",
        "non-classical monocyte",
        "intermediate monocyte",
        "inflammatory monocyte",
        "monocyte-derived macrophage",
        "monocytes",
        "monocyte",
    ],
    "Neutrophil": [
        "neutrophil",
        "neutrophils",
        "pmn",
        "polymorphonuclear neutrophil",
        "low-density neutrophil",
    ],
    "Mast cell": [
        "mast cell",
        "mast cells",
        "tissue mast cell",
    ],
    "Natural killer cell": [
        "natural killer cell",
        "natural killer cells",
        "nk cell",
        "nk cells",
        "innate lymphoid cell",
        "ilc",
        "nk t cell",
    ],
    "CD4+ T cell": [
        "cd4+ t cell",
        "cd4-positive t cell",
        "t helper cell",
        "th1 cell",
        "th2 cell",
        "th17 cell",
        "follicular helper t cell",
        "tfh cell",
        "cd4 t cell",
    ],
    "CD8+ T cell": [
        "cd8+ t cell",
        "cd8-positive t cell",
        "cytotoxic t lymphocyte",
        "ctl",
        "cytotoxic t cell",
        "exhausted t cell",
        "cd8 t cell",
    ],
    "Regulatory T cell": [
        "regulatory t cell",
        "regulatory t cells",
        "treg",
        "tregs",
        "foxp3+ t cell",
        "foxp3-positive t cell",
    ],
    "T cell": [
        "t lymphocyte",
        "t lymphocytes",
        "t-cell",
        "t cells",
        "t cell",
    ],
    "B cell": [
        "germinal center b cell",
        "naive b cell",
        "memory b cell",
        "b lymphocyte",
        "b lymphocytes",
        "b-cell",
        "b cells",
        "b cell",
    ],
    "Plasma cell": [
        "plasma cell",
        "plasma cells",
        "antibody-secreting cell",
        "plasmablast",
        "long-lived plasma cell",
    ],

    # ── ADIPOSE ───────────────────────────────────────────────────────────────
    "Adipocyte": [
        "adipocyte",
        "adipocytes",
        "fat cell",
        "mature adipocyte",
        "white adipocyte",
        "beige adipocyte",
        "brown adipocyte",
    ],
    "Adipose stromal cell": [
        "adipose stromal cell",
        "adipose stromal vascular",
        "preadipocyte",
        "adipose-derived stem cell",
    ],

    # ── EPITHELIAL GENERIC ───────────────────────────────────────────────────
    "Epithelial": [
        "epithelial cell",
        "epithelial cells",
    ],
}


def load_canonical_cell_types() -> list[str]:
    """Return canonical cell type names from marker_rules.json plus lexicon keys."""
    with open(_MARKER_RULES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    rule_types = list(data.keys())
    # Add lexicon-only types not already covered by marker rules
    extra = [ct for ct in LITERATURE_SYNONYMS if ct not in rule_types]
    return rule_types + extra


def _phrases_for_cell_type(canonical: str) -> list[str]:
    phrases = [canonical.lower()]
    phrases.extend(s.lower() for s in LITERATURE_SYNONYMS.get(canonical, []))
    return sorted(set(phrases), key=len, reverse=True)


def population_phrases(canonical: str, limit: int = 4) -> list[str]:
    """Distinct, search-friendly phrases naming a cell population (for PubMed).

    Canonical name first, then the most concise synonyms (shorter phrases recall
    more papers), de-duplicating singular/plural variants.
    """
    synonyms = sorted(LITERATURE_SYNONYMS.get(canonical, []), key=len)
    seen: set[str] = set()
    out: list[str] = []
    for p in [canonical, *synonyms]:
        key = p.lower().rstrip("s")
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return out


def infer_cell_type_from_literature(
    text: str,
    canonical_types: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """
    Infer cell type from paper text using literature phrases.

    Returns (canonical_cell_type, matched_phrase) or (None, None).
    matched_phrase is the raw text that triggered the match and may be more
    specific than the canonical name (e.g. 'kupffer cells' vs 'Macrophage').
    """
    if not text.strip():
        return None, None

    canonical_types = canonical_types or load_canonical_cell_types()
    lower = text.lower()
    matches: list[tuple[str, str, int, int]] = []

    for canonical in canonical_types:
        for phrase in _phrases_for_cell_type(canonical):
            m = re.search(r"\b" + re.escape(phrase) + r"\b", lower)
            if m:
                matches.append((canonical, phrase, m.start(), len(phrase)))

    if not matches:
        return None, None

    # Prefer earliest mention in text; break ties with longer phrase match.
    matches.sort(key=lambda item: (item[2], -item[3]))
    best = matches[0]
    return best[0], best[1]


def get_cell_type_phrases(canonical: str) -> list[str]:
    """Return all known lowercase phrases for a canonical cell type (for co-mention search)."""
    return _phrases_for_cell_type(canonical)


def is_generic_cell_type(canonical: str) -> bool:
    """Return True for broad lineage labels that appear in almost every paper."""
    return canonical in _GENERIC_CANONICAL


def infer_all_cell_types_from_literature(
    text: str,
    canonical_types: list[str] | None = None,
) -> list[tuple[str, str]]:
    """
    Return all distinct (canonical_cell_type, matched_phrase) pairs found in text.

    Unlike ``infer_cell_type_from_literature`` which returns only the earliest
    match, this returns every cell type mentioned so papers describing multiple
    populations (e.g. Aberrant Basaloid AND AT2) produce one row per population.

    Results are ordered by position of first mention in the text.
    """
    if not text.strip():
        return []

    canonical_types = canonical_types or load_canonical_cell_types()
    lower = text.lower()

    # best match per canonical type: earliest pos, then longest phrase
    best: dict[str, tuple[str, str, int, int]] = {}
    for canonical in canonical_types:
        for phrase in _phrases_for_cell_type(canonical):
            m = re.search(r"\b" + re.escape(phrase) + r"\b", lower)
            if m is None:
                continue
            pos = m.start()
            prev = best.get(canonical)
            if prev is None or pos < prev[2] or (pos == prev[2] and len(phrase) > prev[3]):
                best[canonical] = (canonical, phrase, pos, len(phrase))

    return [(v[0], v[1]) for v in sorted(best.values(), key=lambda x: x[2])]


def best_display_name(canonical: str | None, matched_phrase: str | None) -> str:
    """Return the most specific display name for a cell type.

    When the canonical name is a broad lineage label (e.g. 'Macrophage') and
    the matched phrase from the paper is more specific (e.g. 'kupffer cells'),
    the phrase is used—title-cased—as the display name.  This gives 'Kupffer
    Cells' instead of the uninformative 'Macrophage'.
    """
    if not canonical:
        return ""
    if (
        canonical in _GENERIC_CANONICAL
        and matched_phrase
        and len(matched_phrase) > len(canonical)
    ):
        return matched_phrase.title()
    return canonical
