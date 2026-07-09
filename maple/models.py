"""MAPLE data models."""
from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator

# --- Input ---

class AnalysisInput(BaseModel):
    markers: list[str]          # REQUIRED — normalized uppercase gene symbols
    tissue: Optional[str] = None
    disease: Optional[str] = None
    species: Optional[str] = None
    technology: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("markers")
    @classmethod
    def markers_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("markers must not be empty — provide at least one gene symbol")
        return v

    @property
    def has_context(self) -> bool:
        """True when the user supplied tissue/disease/species to focus retrieval."""
        return any(
            (v or "").strip()
            for v in (self.tissue, self.disease, self.species)
        )

# --- Retrieval ---

class RetrievedPaper(BaseModel):
    pmid: str
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    title: str
    journal: Optional[str] = None
    publication_date: Optional[str] = None   # "YYYY-MM-DD" or "YYYY"
    publication_year: Optional[int] = None
    abstract: Optional[str] = None
    full_text: Optional[str] = None          # PMC full text if available
    source_url: str = ""
    retrieval_query: str = ""
    retrieval_rank: int = 0
    retrieval_reason: str = ""

class RetrievalResult(BaseModel):
    retrieved_papers: list[RetrievedPaper] = Field(default_factory=list)
    total_searched: int = 0
    total_after_dedup: int = 0
    fulltext_count: int = 0
    queries_used: list[str] = Field(default_factory=list)
    audit_notes: list[str] = Field(default_factory=list)

# --- Evidence ---

ContextMatch = Literal["matched", "mismatched", "unknown", "not_provided"]
MatchStrength = Literal["High", "Medium", "Low"]
CandidateSpecificity = Literal["specific", "intermediate", "broad"]
EvidenceSection = Literal["abstract", "results", "figure", "table", "supplement", "unknown"]
EvidenceType = Literal[
    "direct_marker_celltype_assignment",
    "cluster_annotation",
    "differential_expression_only",
    "gene_mention_only",
    "celltype_mention_only",
    "unrelated",
]

class ContextMatchDetail(BaseModel):
    species: ContextMatch = "not_provided"
    tissue: ContextMatch = "not_provided"
    disease: ContextMatch = "not_provided"
    technology: ContextMatch = "not_provided"

class EvidenceRow(BaseModel):
    pmid: str
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    paper_title: str
    journal: Optional[str] = None
    publication_date: Optional[str] = None
    publication_year: Optional[int] = None
    celltype_label: str            # specific cell type or cell state FROM THE PAPER
    matched_user_genes: list[str]  # subset of user-provided genes
    number_of_user_genes_found: int
    evidence_snippet: str          # source-grounded short excerpt
    evidence_section: EvidenceSection = "unknown"
    species: Optional[str] = None
    tissue: Optional[str] = None
    disease: Optional[str] = None
    technology: Optional[str] = None
    match_strength: MatchStrength = "Low"
    evidence_type: EvidenceType = "gene_mention_only"
    # Cell-type-name string the LLM considers canonical for grouping (lowercased).
    normalized_label: str = ""
    # Whether the paper describes the matched genes as specific/defining for the
    # cell type (LLM judgement), and how specific the label itself is.
    marker_specific: bool = False
    specificity: CandidateSpecificity = "intermediate"
    match_reason: str = ""
    context_match: ContextMatchDetail = Field(default_factory=ContextMatchDetail)
    source_url: str = ""

class ExtractionResult(BaseModel):
    evidence_rows: list[EvidenceRow] = Field(default_factory=list)
    excluded_paper_count: int = 0
    excluded_reasons: list[str] = Field(default_factory=list)
    audit_notes: list[str] = Field(default_factory=list)
    papers_with_direct_assignment: int = 0
    papers_with_cluster_annotation: int = 0
    papers_de_only: int = 0
    papers_gene_mention_only: int = 0
    papers_no_celltype_assignment: int = 0
    abstract_only_papers: int = 0
    validation_rejected_rows: int = 0

# --- Candidates ---

class CandidateLabel(BaseModel):
    candidate_label: str
    normalized_label: str
    original_paper_labels: list[str] = Field(default_factory=list)
    supporting_genes: list[str] = Field(default_factory=list)
    supporting_pmids: list[str] = Field(default_factory=list)
    supporting_paper_count: int = 0
    best_evidence_row_indices: list[int] = Field(default_factory=list)
    specificity: CandidateSpecificity = "intermediate"
    candidate_score: float = 0.0
    candidate_rationale: str = ""

class CandidateResult(BaseModel):
    candidate_labels: list[CandidateLabel] = Field(default_factory=list)
    audit_notes: list[str] = Field(default_factory=list)

# --- Devil's Advocate ---

ConfidenceAdjustment = Literal["raise", "keep", "lower"]

class AlternativeLabel(BaseModel):
    label: str
    reason: str
    supporting_genes: list[str] = Field(default_factory=list)
    supporting_pmids: list[str] = Field(default_factory=list)

class DevilsAdvocateResult(BaseModel):
    critique_summary: str = ""
    strongest_counterargument: str = ""
    possible_alternative_labels: list[AlternativeLabel] = Field(default_factory=list)
    nonspecific_genes: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    context_mismatches: list[str] = Field(default_factory=list)
    recommended_confidence_adjustment: ConfidenceAdjustment = "keep"
    confidence_adjustment_reason: str = ""
    additional_markers_needed: list[str] = Field(default_factory=list)
    additional_context_needed: list[str] = Field(default_factory=list)
    audit_notes: list[str] = Field(default_factory=list)

# --- Consensus ---

Confidence = Literal["High", "Medium", "Low", "Insufficient"]

class ConsensusAlternative(BaseModel):
    label: str
    reason_not_selected: str
    supporting_genes: list[str] = Field(default_factory=list)
    supporting_pmids: list[str] = Field(default_factory=list)

class ConsensusResult(BaseModel):
    consensus_label: str = "Insufficient evidence"
    confidence: Confidence = "Insufficient"
    consensus_rationale: str = ""
    supporting_genes: list[str] = Field(default_factory=list)
    main_supporting_pmids: list[str] = Field(default_factory=list)
    main_supporting_papers: list[str] = Field(default_factory=list)
    alternative_labels: list[ConsensusAlternative] = Field(default_factory=list)
    devils_advocate_critique: str = ""
    what_would_improve_confidence: list[str] = Field(default_factory=list)
    audit_notes: list[str] = Field(default_factory=list)

# --- Full State ---

class TableState(BaseModel):
    page: int = 1
    page_size: int = 20
    sort_by: str = "number_of_user_genes_found"
    sort_direction: str = "desc"
    search_query: str = ""

class AuditEntry(BaseModel):
    agent: str
    message: str
    detail: str = ""

class AnalysisState(BaseModel):
    input: AnalysisInput
    retrieval: Optional[RetrievalResult] = None
    extraction: Optional[ExtractionResult] = None
    candidates: Optional[CandidateResult] = None
    devils_advocate: Optional[DevilsAdvocateResult] = None
    consensus: Optional[ConsensusResult] = None
    table_state: TableState = Field(default_factory=TableState)
    audit_trail: list[AuditEntry] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
