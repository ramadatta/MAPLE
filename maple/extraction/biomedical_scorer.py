"""Biomedical evidence scorer interface (Phase 2 stub).

Default implementation is rule-based via evidence_signals.py.
Future: PubMedBERT, Bioformer, or other encoder backends.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from maple import config as cfg
from maple.extraction.evidence_signals import EvidenceSignal, score_evidence_chunk
from maple.models import AnalysisInput


class BiomedicalEvidenceScorer(ABC):
    @abstractmethod
    def score_chunks(
        self,
        chunks: list[tuple[str, str, str]],
        user_genes: list[str],
        analysis_input: AnalysisInput | None,
    ) -> list[EvidenceSignal]:
        """
        Score text chunks.

        Each chunk is (paper_id, chunk_id, text).
        """


class RuleBasedEvidenceScorer(BiomedicalEvidenceScorer):
    """Default rule-based scorer (no ML dependencies)."""

    def score_chunks(
        self,
        chunks: list[tuple[str, str, str]],
        user_genes: list[str],
        analysis_input: AnalysisInput | None,
    ) -> list[EvidenceSignal]:
        signals: list[EvidenceSignal] = []
        for paper_id, chunk_id, text in chunks:
            signals.append(
                score_evidence_chunk(
                    text,
                    user_genes,
                    analysis_input,
                    paper_id=paper_id,
                    chunk_id=chunk_id,
                )
            )
        signals.sort(key=lambda s: s.assignment_score, reverse=True)
        return signals


def get_evidence_scorer() -> BiomedicalEvidenceScorer:
    """Return configured scorer; encoder backends plug in here later."""
    if cfg.BIOMEDICAL_ENCODER_ENABLED and cfg.BIOMEDICAL_ENCODER_MODEL:
        # Phase 2: load transformer-backed scorer when implemented
        pass
    return RuleBasedEvidenceScorer()
