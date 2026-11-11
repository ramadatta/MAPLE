"""Small Pydantic schemas used ONLY for LLM JSON output parsing."""
from __future__ import annotations

from pydantic import BaseModel, Field


class _LLMEvidenceRow(BaseModel):
    celltype_label: str
    normalized_label: str = ""
    matched_user_genes: list[str]
    evidence_snippet: str
    marker_specific: bool = False
    specificity: str = "intermediate"
    evidence_type: str = "direct_marker_celltype_assignment"
    match_strength: str = "Low"
    evidence_section: str = "unknown"
    # Biological context of the cell type AS STATED in this paper ("" if not stated).
    tissue: str = ""
    disease: str = ""
    species: str = ""


class _LLMEvidenceOutput(BaseModel):
    rows: list[_LLMEvidenceRow] = Field(default_factory=list)


class _LLMCandidateConsensus(BaseModel):
    consensus_label: str
    confidence: str
    consensus_rationale: str
    supporting_genes: list[str] = Field(default_factory=list)
    main_supporting_pmids: list[str] = Field(default_factory=list)
    main_supporting_papers: list[str] = Field(default_factory=list)
    alternative_labels: list[dict] = Field(default_factory=list)
    devils_advocate_critique: str = ""
    what_would_improve_confidence: list[str] = Field(default_factory=list)
