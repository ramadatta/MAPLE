"""Fields: normalise a paper's tissue / disease / species and place it relative to
the user's own field.

A *field* is (organ, disease, species). Every evidence row gets one, built from
what the paper itself states (extracted by the LLM). When the user names their
own field ("home"), each row is placed in a ring:

    home      same field as the user
    near      same organ (or organ system) but another disease or species,
              or the same disease in an unstated organ
    far       another organ system, or another disease when only a disease was given
    unplaced  the paper does not state enough to tell

The vocabularies below only normalise *context* names (organs, diseases, species);
they never decide a cell-type label, which always comes from the paper text.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel

Ring = Literal["home", "near", "far", "unplaced"]

# ── Organs: canonical name → (organ system, synonyms) ──────────────────────────
# Multi-word synonyms are matched before single words. Order within a system is
# irrelevant; the longest matching synonym wins.
_ORGANS: dict[str, tuple[str, tuple[str, ...]]] = {
    "lung": ("respiratory", ("lung", "lungs", "pulmonary", "alveolar", "alveoli", "alveolus", "pleura", "pleural", "pneumocyte", "pneumocytes")),
    "airway": ("respiratory", ("airway", "airways", "bronchial", "bronchus", "bronchi", "bronchiole", "bronchioles", "bronchiolar", "trachea", "tracheal", "nasal", "nasopharynx", "upper airway", "small airway", "small airways")),
    "heart": ("cardiovascular", ("heart", "cardiac", "myocardium", "myocardial", "ventricle", "ventricular", "atrium", "atrial")),
    "blood vessels": ("cardiovascular", ("artery", "arteries", "arterial", "aorta", "aortic", "vein", "veins", "vasculature", "blood vessel", "blood vessels")),
    "blood": ("blood and immune", ("blood", "pbmc", "pbmcs", "peripheral blood", "circulating")),
    "bone marrow": ("blood and immune", ("bone marrow", "marrow")),
    "lymph node": ("blood and immune", ("lymph node", "lymph nodes", "lymphatic")),
    "spleen": ("blood and immune", ("spleen", "splenic")),
    "thymus": ("blood and immune", ("thymus", "thymic")),
    "tonsil": ("blood and immune", ("tonsil", "tonsils", "tonsillar")),
    "liver": ("digestive", ("liver", "livers", "hepatic")),
    "bile duct": ("digestive", ("bile duct", "bile ducts", "biliary", "gallbladder")),
    "pancreas": ("digestive", ("pancreas", "pancreatic", "islet", "islets")),
    "stomach": ("digestive", ("stomach", "gastric")),
    "esophagus": ("digestive", ("esophagus", "oesophagus", "esophageal", "oesophageal")),
    "small intestine": ("digestive", ("small intestine", "small intestinal", "intestine", "intestinal", "ileum", "ileal", "jejunum", "duodenum", "duodenal", "gut")),
    "colon": ("digestive", ("colon", "colonic", "colorectal", "rectum", "rectal", "large intestine")),
    "oral cavity": ("head and neck", ("oral", "oral cavity", "tongue", "gingiva", "gingival", "salivary", "salivary gland")),
    "head and neck": ("head and neck", ("head and neck", "oropharynx", "oropharyngeal", "larynx", "laryngeal", "pharynx", "pharyngeal", "hnscc")),
    "kidney": ("urinary", ("kidney", "kidneys", "renal", "nephron", "nephrons", "glomerulus", "glomeruli", "glomerular", "proximal tubule", "proximal tubules")),
    "bladder": ("urinary", ("bladder", "urothelium", "urothelial")),
    "prostate": ("reproductive", ("prostate", "prostatic")),
    "breast": ("reproductive", ("breast", "mammary")),
    "ovary": ("reproductive", ("ovary", "ovaries", "ovarian")),
    "uterus": ("reproductive", ("uterus", "uterine", "endometrium", "endometrial", "cervix", "cervical")),
    "testis": ("reproductive", ("testis", "testes", "testicular")),
    "placenta": ("reproductive", ("placenta", "placental", "decidua", "trophoblast")),
    "brain": ("nervous", ("brain", "cerebral", "hippocampus", "hippocampal", "cerebellum", "cerebellar", "neocortex", "striatum", "hypothalamus")),
    "spinal cord": ("nervous", ("spinal cord",)),
    "retina": ("eye", ("retina", "retinal")),
    "eye": ("eye", ("eye", "eyes", "cornea", "corneal", "conjunctiva", "lens")),
    "skin": ("skin", ("skin", "epidermis", "epidermal", "dermis", "dermal", "cutaneous", "hair follicle", "hair follicles", "wound")),
    "skeletal muscle": ("musculoskeletal", ("skeletal muscle", "muscle", "muscles")),
    "bone": ("musculoskeletal", ("bone", "bones", "skeletal")),
    "cartilage": ("musculoskeletal", ("cartilage", "chondral")),
    "joint": ("musculoskeletal", ("joint", "joints", "synovium", "synovial")),
    "adipose tissue": ("adipose", ("adipose", "adipose tissue", "fat")),
    "thyroid": ("endocrine", ("thyroid",)),
    "adrenal gland": ("endocrine", ("adrenal", "adrenal gland")),
}

# ── Diseases: (patterns, canonical name, short display) ────────────────────────
_DISEASES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("idiopathic pulmonary fibrosis", "ipf"), "idiopathic pulmonary fibrosis", "IPF"),
    (("chronic obstructive pulmonary disease", "copd"), "chronic obstructive pulmonary disease", "COPD"),
    (("pancreatic ductal adenocarcinoma", "pdac"), "pancreatic ductal adenocarcinoma", "PDAC"),
    (("head and neck squamous cell carcinoma", "hnscc"), "head and neck squamous cell carcinoma", "HNSCC"),
    (("non-small cell lung cancer", "non-small-cell lung cancer", "nsclc"), "non-small cell lung cancer", "NSCLC"),
    (("lung adenocarcinoma", "luad"), "lung adenocarcinoma", "LUAD"),
    (("hepatocellular carcinoma", "hcc"), "hepatocellular carcinoma", "HCC"),
    (("acute kidney injury", "aki"), "acute kidney injury", "AKI"),
    (("chronic kidney disease", "ckd"), "chronic kidney disease", "CKD"),
    (("non-alcoholic steatohepatitis", "nonalcoholic steatohepatitis", "metabolic dysfunction-associated steatohepatitis", "nash", "mash"), "steatohepatitis (MASH/NASH)", "MASH"),
    (("non-alcoholic fatty liver disease", "nonalcoholic fatty liver disease", "masld", "nafld"), "fatty liver disease (MASLD/NAFLD)", "MASLD"),
    (("covid-19", "covid19", "covid", "sars-cov-2"), "COVID-19", "COVID-19"),
    (("ulcerative colitis",), "ulcerative colitis", "UC"),
    (("crohn's disease", "crohn disease", "crohns disease", "crohn"), "Crohn's disease", "Crohn's"),
    (("systemic sclerosis", "scleroderma"), "systemic sclerosis", "SSc"),
    (("rheumatoid arthritis",), "rheumatoid arthritis", "RA"),
    (("multiple sclerosis",), "multiple sclerosis", "MS"),
    (("alzheimer's disease", "alzheimer disease", "alzheimers disease", "alzheimer"), "Alzheimer's disease", "Alzheimer's"),
    (("bleomycin",), "bleomycin injury", "bleomycin injury"),
    (("healthy", "normal", "control", "controls", "steady state", "steady-state", "homeostasis", "homeostatic", "unaffected", "non-diseased", "donor"), "healthy", "healthy"),
)

# Disease groups, checked in order (first hit wins).
_DISEASE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("healthy", ("healthy",)),
    ("fibrosis", ("fibrosis", "fibrotic", "cirrhosis", "scleroderma", "systemic sclerosis", "ipf")),
    ("cancer", ("cancer", "carcinoma", "adenocarcinoma", "tumor", "tumour", "neoplas", "malignan", "metasta",
                "melanoma", "glioma", "glioblastoma", "lymphoma", "leukemia", "leukaemia", "sarcoma", "myeloma", "blastoma",
                "pdac", "hnscc", "nsclc", "hcc", "luad")),
    ("infection", ("infection", "infected", "covid", "sars-cov", "virus", "viral", "bacterial", "sepsis", "influenza",
                   "tuberculosis", "pneumonia")),
    ("injury and repair", ("injury", "injured", "damage", "wound", "ischemi", "ischaemi", "bleomycin", "naphthalene",
                           "regenerat", "repair", "aki")),
    ("chronic inflammation", ("inflamm", "colitis", "crohn", "arthritis", "lupus", "asthma", "psoriasis", "dermatitis",
                              "autoimmun", "copd", "chronic obstructive", "emphysema")),
    ("metabolic", ("diabet", "obesity", "obese", "steato", "nafld", "masld", "nash", "mash", "metabolic")),
    ("neurodegeneration", ("alzheimer", "parkinson", "neurodegener", "multiple sclerosis", "als ", "huntington")),
    ("cardiovascular", ("infarction", "heart failure", "atherosclero", "cardiomyopathy", "hypertension")),
    ("chronic kidney disease", ("ckd", "chronic kidney", "nephropathy", "glomerulonephritis")),
    ("development", ("development", "developing", "embryo", "embryonic", "fetal", "foetal", "neonatal")),
    ("aging", ("aging", "ageing", "aged ")),
)

_SPECIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Human", ("human", "humans", "homo sapiens", "patient", "patients", "donor", "donors")),
    ("Mouse", ("mouse", "mice", "murine", "mus musculus")),
    ("Rat", ("rat", "rats", "rattus")),
    ("Zebrafish", ("zebrafish", "danio rerio")),
    ("Pig", ("pig", "pigs", "porcine", "swine")),
    ("Non-human primate", ("macaque", "macaques", "monkey", "monkeys", "primate", "primates")),
)

_EMPTY = {"", "n/a", "na", "none", "not stated", "unknown", "not specified", "unspecified", "-"}


class FieldContext(BaseModel):
    """Normalised field of a paper (or of the user)."""

    organ: Optional[str] = None
    organ_system: Optional[str] = None
    disease: Optional[str] = None        # canonical disease name
    disease_short: Optional[str] = None  # short display form, e.g. "IPF"
    disease_group: Optional[str] = None
    species: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not (self.organ or self.disease or self.species)

    @property
    def key(self) -> str:
        return "|".join([self.organ or "?", self.disease or "?", self.species or "?"])

    @property
    def label(self) -> str:
        parts = [self.organ.capitalize() if self.organ else None, self.disease_short, self.species]
        shown = [p for p in parts if p]
        return " · ".join(shown) if shown else "Not stated"


def _clean(text: Optional[str]) -> str:
    text = (text or "").strip().lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return "" if text in _EMPTY else re.sub(r"\s+", " ", text)


def _has(text: str, phrase: str) -> bool:
    """Whole-word (or whole-phrase) match; phrases ending in a letter use word boundaries."""
    if phrase.endswith(" "):
        return phrase in text + " "
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def _prefix(text: str, stem: str) -> bool:
    """Match a word that starts with ``stem`` (e.g. 'ischemi' → 'ischemic')."""
    if stem.endswith(" "):
        return stem in text + " "
    return re.search(r"(?<![a-z0-9])" + re.escape(stem), text) is not None


def detect_organ(text: Optional[str]) -> Optional[str]:
    """Most specific organ named in ``text`` (longest matching synonym wins)."""
    text = _clean(text)
    if not text:
        return None
    best: tuple[int, Optional[str]] = (0, None)
    for organ, (_system, synonyms) in _ORGANS.items():
        for syn in synonyms:
            if len(syn) > best[0] and _has(text, syn):
                best = (len(syn), organ)
    return best[1]


def organs_in_text(text: Optional[str]) -> set[str]:
    """Every organ mentioned anywhere in ``text``."""
    text = _clean(text)
    found: set[str] = set()
    for organ, (_system, synonyms) in _ORGANS.items():
        if any(_has(text, syn) for syn in synonyms):
            found.add(organ)
    return found


def organ_system(organ: Optional[str]) -> Optional[str]:
    return _ORGANS[organ][0] if organ in _ORGANS else None


def normalize_disease(text: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (canonical, short display, group) for a free-text disease."""
    text = _clean(text)
    if not text:
        return None, None, None
    canonical, short = None, None
    for patterns, canon, disp in _DISEASES:
        if any(_has(text, p) for p in patterns):
            canonical, short = canon, disp
            break
    if canonical is None:
        canonical = text
        short = text if len(text) <= 32 else text[:31] + "…"
    group = None
    probe = f"{text} {canonical.lower()}"
    for name, stems in _DISEASE_GROUPS:
        if any(_prefix(probe, s) for s in stems):
            group = name
            break
    return canonical, short, group


def normalize_species(text: Optional[str]) -> Optional[str]:
    text = _clean(text)
    if not text:
        return None
    for name, synonyms in _SPECIES:
        if any(_has(text, s) for s in synonyms):
            return name
    return text.capitalize()


def normalize_context(
    tissue: Optional[str] = None,
    disease: Optional[str] = None,
    species: Optional[str] = None,
) -> FieldContext:
    """Normalise free-text context. The organ falls back to the disease name
    ("idiopathic pulmonary fibrosis" → lung) when no tissue is stated."""
    canonical, short, group = normalize_disease(disease)
    organ = detect_organ(tissue) or detect_organ(disease)
    if organ is None and _clean(tissue):
        organ = _clean(tissue)  # keep unrecognised tissues as stated
    return FieldContext(
        organ=organ,
        organ_system=organ_system(organ),
        disease=canonical,
        disease_short=short,
        disease_group=group,
        species=normalize_species(species),
    )


# ── Placing a field relative to the user's home field ──────────────────────────

def _compare_organ(row: FieldContext, home: FieldContext) -> str:
    if not row.organ:
        return "unknown"
    if row.organ == home.organ:
        return "match"
    # Unrecognised tissue names ("honeycomb cysts", "organoids") cannot be placed
    # reliably, so they never make a paper "far" on their own.
    if row.organ not in _ORGANS or home.organ not in _ORGANS:
        return "unknown"
    if row.organ_system and row.organ_system == home.organ_system:
        return "related"
    return "mismatch"


def _compare_disease(row: FieldContext, home: FieldContext) -> str:
    if not row.disease:
        return "unknown"
    if row.disease == home.disease:
        return "match"
    # One name contained in the other counts as the same disease:
    # "fibrosis" (home) covers "liver fibrosis"; "pulmonary fibrosis" (row) is the
    # broader name papers often use for IPF.
    if home.disease:
        a, b = home.disease.lower(), row.disease.lower()
        for inner, outer in ((a, b), (b, a)):
            if re.search(r"(?<![a-z])" + re.escape(inner) + r"(?![a-z])", outer):
                return "match"
    if row.disease_group and row.disease_group == home.disease_group:
        return "related"
    return "mismatch"


def _compare_species(row: FieldContext, home: FieldContext) -> str:
    if not row.species:
        return "unknown"
    return "match" if row.species == home.species else "mismatch"


def classify_ring(row: FieldContext, home: Optional[FieldContext]) -> Optional[Ring]:
    """Place ``row`` relative to ``home``. Returns None when no home field is set."""
    if home is None or home.is_empty:
        return None
    results: dict[str, str] = {}
    if home.organ:
        results["organ"] = _compare_organ(row, home)
    if home.disease:
        results["disease"] = _compare_disease(row, home)
    if home.species:
        results["species"] = _compare_species(row, home)

    # A different organ system is far, whatever the disease.
    if results.get("organ") == "mismatch":
        return "far"
    # With only a disease given, an unrelated disease is far.
    if "organ" not in results and results.get("disease") == "mismatch":
        return "far"

    place_keys = [k for k in ("organ", "disease") if k in results]
    if place_keys and all(results[k] == "unknown" for k in place_keys):
        return "unplaced"
    if not place_keys and results.get("species") == "unknown":
        return "unplaced"

    if all(v in ("match", "unknown") for v in results.values()):
        return "home"
    return "near"


def home_from_input(tissue: Optional[str], disease: Optional[str], species: Optional[str]) -> Optional[FieldContext]:
    home = normalize_context(tissue, disease, species)
    return None if home.is_empty else home


def text_matches_home(text: str, home: Optional[FieldContext]) -> bool:
    """Cheap pre-check (title/abstract) used to balance which papers get read."""
    if home is None:
        return False
    if home.organ and home.organ in organs_in_text(text):
        return True
    if home.disease and home.disease != "healthy":
        lowered = _clean(text)
        patterns = next((p for p, canon, _ in _DISEASES if canon == home.disease), (home.disease.lower(),))
        if any(_has(lowered, p) for p in patterns):
            return True
    return False
