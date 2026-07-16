"""LLM prompt templates for MAPLE evidence extraction and consensus."""
from __future__ import annotations

# Bump when prompt wording/schema-shaping changes. Recorded in run provenance so
# results are traceable to the exact prompt version that produced them.
PROMPT_VERSION = "1.0"

# ---- Evidence Extraction Prompt ----

EVIDENCE_EXTRACTION_SYSTEM = (
    "You are a careful scientific literature analyst. Read the provided paper text and "
    "decide, for the user's marker genes, whether the paper describes those genes as markers "
    "of a specific cell type, subtype, cluster, or cell state. Base every judgement ONLY on "
    "the text provided — never invent cell types, associations, or evidence."
)

EVIDENCE_EXTRACTION_USER_TEMPLATE = """\
Paper PMID: {pmid}
Title: {title}
Abstract: {abstract}
{fulltext_section}

User-provided marker genes: {genes}

Task: Identify every place in THIS paper where one or more of the user genes are described as \
markers of (or associated with) a named cell type, subtype, cluster, or cell state. Work tissue- \
and disease-agnostically: the cell type can be from any organ, species, or condition. Do not rely \
on any predefined list of cell types — use whatever the paper actually names.

For each association return an object with:
1. celltype_label   — the cell type/state exactly as the paper names it.
2. normalized_label — a lowercase canonical form for grouping equivalent names across papers \
(e.g. "at2 cell", "alveolar type II epithelial cell" -> "alveolar type 2 cell").
3. matched_user_genes — ALL the user genes (from the list above) that the paper attributes to \
THIS cell type, even when they are listed across several adjacent sentences or separate marker \
lists (e.g. basal markers in one sentence, EMT markers in the next, senescence markers in a third).
4. evidence_snippet — text copied EXACTLY from the paper, character-for-character (same words, \
punctuation, spelling, gene symbols and order), so the user can paste it into the original paper \
and find it with Ctrl-F. Do NOT paraphrase, summarise, reorder, translate, or fix wording. If the \
genes are described in different places, copy the exact fragment from each place and join the \
separate fragments with " ... " (the ellipsis marks omitted text in between). Every gene in \
matched_user_genes must appear inside one of these exact fragments. Keep it under ~500 characters.
5. marker_specific — true if the paper presents these genes as specific/defining markers for \
this cell type (not merely mentioned or differentially expressed in passing).
6. specificity — "specific" (a defined subtype/state), "intermediate", or "broad" (lineage-level \
such as "epithelial cells" or "immune cells").
7. evidence_type — one of: direct_marker_celltype_assignment, cluster_annotation, \
differential_expression_only, gene_mention_only.
8. match_strength — High / Medium / Low.
9. evidence_section — abstract / results / figure / table / supplement / unknown.
10. tissue — the tissue / organ this cell type is described in, exactly as stated in the paper \
(e.g. "lung", "liver", "peripheral blood"). Use "" if the paper does not state it.
11. disease — the disease / condition context, as stated (e.g. "idiopathic pulmonary fibrosis", \
"healthy", "COVID-19"). Use "" if not stated.
12. species — the organism, as stated (e.g. "human", "mouse", "Mus musculus"). Use "" if not stated.

RULES:
- Produce ONE row per cell type. If the paper assigns several of the user genes to the same cell \
type across multiple sentences, merge them into that single row — do NOT split one cell type's \
marker panel into several rows.
- evidence_snippet MUST be copied from the paper text provided (traceable, not paraphrased), and \
must contain every gene listed in matched_user_genes.
- Do NOT invent cell types that are not named in the paper text.
- Do NOT use background knowledge to assign a cell type the paper does not state.
- Report broad/lineage labels too when that is genuinely what the paper says — do not drop them, \
just mark specificity accordingly.
- If the paper contains no marker-to-celltype association for these genes, return an empty list.

Return JSON: {{"rows": [{{"celltype_label": "...", "normalized_label": "...", \
"matched_user_genes": [...], "evidence_snippet": "...", "marker_specific": true, \
"specificity": "specific|intermediate|broad", \
"evidence_type": "direct_marker_celltype_assignment|cluster_annotation|differential_expression_only|gene_mention_only", \
"match_strength": "High|Medium|Low", "evidence_section": "...", \
"tissue": "...", "disease": "...", "species": "..."}}]}}
"""

EVIDENCE_EXTRACTION_FULLTEXT_PLACEHOLDER = (
    "Full text excerpt:\n{full_text}"
)

# ---- Devil's Advocate Prompt ----

DEVILS_ADVOCATE_SYSTEM = (
    "You are a rigorous scientific reviewer. Your job is to identify weaknesses in the "
    "proposed cell-type annotation. Be skeptical. Identify real problems."
)

DEVILS_ADVOCATE_USER_TEMPLATE = """\
Proposed consensus cell type: {top_candidate_label}
Confidence candidate score: {top_score:.3f}

User-provided genes: {genes}
Evidence summary ({n_rows} rows):
{evidence_summary}

Devil's Advocate tasks:
1. What is the STRONGEST reason this label might be WRONG?
2. Are any user genes nonspecific housekeeping genes that could appear in any cell type?
3. Are there contradictory evidence rows linking the SAME genes to DIFFERENT cell types in different papers?
4. Is the label too broad (lineage) rather than specific (cell subtype)?
5. What alternative cell types are supported for subsets of the marker panel?
6. Should confidence be raised, kept, or lowered?
7. What additional markers would help distinguish between alternatives?

Return JSON matching the DevilsAdvocateResult schema.
"""

# ---- Consensus Prompt ----

CONSENSUS_SYSTEM = (
    "You are a careful computational biologist summarizing LITERATURE DISCOVERY results. "
    "The user wants to know where their marker genes have already been annotated to cell types "
    "across published studies (any tissue or disease). Never invent citations or cell types."
)

CONSENSUS_USER_TEMPLATE = """\
Marker genes submitted: {genes}
{context_note}

Evidence ({n_rows} rows, top candidates):
{candidate_summary}

Devil's Advocate critique:
{critique_summary}
{strongest_counterargument}
Recommended confidence adjustment: {adjustment}

Instructions:
- MAPLE is a discovery tool: summarize where markers are annotated in the literature.
- If different genes or papers support DIFFERENT cell types, prefer the label \
"Multiple cell types in literature" and list the main alternatives — do NOT force a single \
cell type when the panel spans lineages or tissues.
- If one cell type clearly dominates the evidence for most markers, you may name it — but still \
list other reported annotations as alternatives.
- Tissue and disease context come from each paper (titles/abstracts), not from user assumptions.
- Prefer specific cell type labels from papers over generic lineage terms when supported.
- If no specific cell type is supported, say "Insufficient evidence."
- Confidence reflects how coherent the literature is, not whether one disease context matches.
- DO NOT use background marker biology unsupported by the retrieved papers.
- Provide specific supporting PMIDs from the evidence (not invented).

Return JSON matching ConsensusResult schema.
"""
