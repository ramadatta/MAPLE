"""Pydantic schemas for the Evidence-Aware CellType Annotator."""

from __future__ import annotations

from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

ConfidenceLabel = Literal["High", "Medium", "Low", "Insufficient"]
EvidenceType = Literal["Abstract", "Title only", "Metadata only", "No direct evidence"]
MatchStatus = Literal[True, False, "unknown"]
SpeciesOption = Literal["Human", "Mouse", "Other"]


class UserInput(BaseModel):
    """User-provided annotation context."""

    genes: list[str]
    tissue: str = ""
    disease: str = ""
    species: SpeciesOption = "Human"
    papers_per_query: int = Field(default=5, ge=1, le=20)
    parse_warnings: list[str] = Field(default_factory=list)


class MarkerCandidate(BaseModel):
    """A candidate cell type from marker-rule matching."""

    cell_type: str
    matched_genes: list[str]
    missing_expected_genes: list[str]
    confidence_label: ConfidenceLabel
    confidence_score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    ambiguous_markers: list[str] = Field(default_factory=list)


class MarkerAgentResult(BaseModel):
    """Output from the Marker Biology Agent."""

    candidates: list[MarkerCandidate]
    warnings: list[str] = Field(default_factory=list)
    input_genes: list[str] = Field(default_factory=list)


class PubMedQuery(BaseModel):
    """A PubMed search query with metadata."""

    query: str
    gene: str
    candidate_cell_type: Optional[str] = None
    query_type: str
    priority: int = Field(ge=1, le=5)


class PubMedPaper(BaseModel):
    """Retrieved PubMed paper metadata."""

    pmid: str
    title: str
    journal: str = ""
    year: Optional[int] = None
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    pubmed_url: str = ""
    pmcid: str = ""
    doi: str = ""
    is_open_access: bool = False


class GeneEvidence(BaseModel):
    """Literature evidence linking a gene to a cell type."""

    gene: str
    predicted_cell_type: str
    evidence_sentence: str
    evidence_type: EvidenceType
    pmid: str
    paper_title: str
    journal: str = ""
    year: Optional[int] = None
    pubmed_url: str = ""
    tissue_match: MatchStatus = "unknown"
    disease_match: MatchStatus = "unknown"
    species_match: MatchStatus = "unknown"
    confidence_label: ConfidenceLabel
    confidence_score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    literature_inferred: bool = False


class EvidenceAgentResult(BaseModel):
    """Output from the Evidence Extraction Agent."""

    evidence_items: list[GeneEvidence] = Field(default_factory=list)
    genes_with_no_evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReviewerComment(BaseModel):
    """A single reviewer caveat or observation."""

    category: str
    comment: str
    severity: Literal["info", "warning", "critical"] = "warning"


class ReviewerResult(BaseModel):
    """Output from the Reviewer Agent."""

    key_caveats: list[str] = Field(default_factory=list)
    missing_markers: list[str] = Field(default_factory=list)
    alternative_annotations: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    reviewer_summary: str = ""
    confidence_adjustment: float = Field(default=0.0, ge=-0.3, le=0.3)
    comments: list[ReviewerComment] = Field(default_factory=list)


class ConsensusResult(BaseModel):
    """Final consensus annotation."""

    final_cell_type: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    confidence_label: ConfidenceLabel
    evidence_genes: list[str] = Field(default_factory=list)
    supporting_pmids: list[str] = Field(default_factory=list)
    strongest_supporting_papers: list[str] = Field(default_factory=list)
    conflicting_or_weak_evidence: list[str] = Field(default_factory=list)
    missing_markers: list[str] = Field(default_factory=list)
    alternative_annotations: list[str] = Field(default_factory=list)
    umap_label: str = ""
    biological_interpretation: str = ""
    concise_summary: str = ""
    recommended_next_markers: list[str] = Field(default_factory=list)
    marker_based_inference: str = ""
    pubmed_evidence_summary: str = ""
    reviewer_caveats_summary: str = ""


class PaperCoverage(BaseModel):
    """A retrieved paper ranked by how many input markers it describes."""

    pmid: str
    title: str
    journal: str = ""
    year: Optional[int] = None
    pubmed_url: str = ""
    cell_type: str = ""
    matched_markers: list[str] = Field(default_factory=list)
    marker_count: int = 0
    # Genes found in sentences that also mention the named cell type (attributed genes).
    cell_type_specific_genes: list[str] = Field(default_factory=list)
    composite_score: float = 0.0
    evidence_sentence: str = ""
    full_text_used: bool = False


class FinalReport(BaseModel):
    """Complete report combining all agent outputs."""

    user_input: UserInput
    marker_result: MarkerAgentResult
    evidence_result: EvidenceAgentResult
    reviewer_result: ReviewerResult
    consensus: ConsensusResult
    ranked_papers: list[PaperCoverage] = Field(default_factory=list)
    pubmed_warnings: list[str] = Field(default_factory=list)

    def evidence_dataframe(self) -> pd.DataFrame:
        """Flatten evidence items into a pandas DataFrame for display and CSV export."""
        rows = []
        for item in self.evidence_result.evidence_items:
            rows.append(
                {
                    "Gene": item.gene,
                    "Predicted Cell Type": item.predicted_cell_type,
                    "Confidence": item.confidence_label,
                    "Evidence Type": item.evidence_type,
                    "PMID": item.pmid,
                    "Paper Title": item.paper_title,
                    "Year": str(item.year) if item.year is not None else "",
                    "Tissue Match": str(item.tissue_match),
                    "Disease Match": str(item.disease_match),
                    "Evidence Sentence": item.evidence_sentence,
                    "PubMed URL": item.pubmed_url,
                }
            )
        if not rows:
            return pd.DataFrame(
                columns=[
                    "Gene",
                    "Predicted Cell Type",
                    "Confidence",
                    "Evidence Type",
                    "PMID",
                    "Paper Title",
                    "Year",
                    "Tissue Match",
                    "Disease Match",
                    "Evidence Sentence",
                    "PubMed URL",
                ]
            )
        return pd.DataFrame(rows)
