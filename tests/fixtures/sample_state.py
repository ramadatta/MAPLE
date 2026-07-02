"""Pre-built AnalysisState fixtures for tests."""
from maple.models import (
    AnalysisInput, AnalysisState, RetrievalResult, RetrievedPaper,
    ExtractionResult, EvidenceRow, ContextMatchDetail,
    CandidateResult, CandidateLabel, DevilsAdvocateResult,
    ConsensusResult, TableState,
)


def make_fibrosis_fibroblast_state() -> AnalysisState:
    """AT2/IPF fibroblast-like scenario with strong evidence."""
    inp = AnalysisInput(
        markers=["COL1A1", "COL3A1", "POSTN", "CTHRC1", "DCN", "LUM"],
        tissue="lung",
        disease="IPF",
    )

    daf_rows = [
        EvidenceRow(
            pmid=f"1000{i}",
            paper_title=f"IPF single-cell fibrosis atlas study {i}",
            celltype_label="disease-associated fibroblast",
            matched_user_genes=["COL1A1", "COL3A1", "POSTN"],
            number_of_user_genes_found=3,
            evidence_snippet=(
                "COL1A1, COL3A1, and POSTN mark disease-associated fibroblasts "
                "in IPF lung tissue as shown by single-cell RNA sequencing."
            ),
            match_strength="High",
            publication_year=2022,
            tissue="lung",
            disease="IPF",
        )
        for i in range(1, 4)
    ]
    myo_row = EvidenceRow(
        pmid="20001",
        paper_title="Myofibroblast activation in pulmonary fibrosis",
        celltype_label="myofibroblast",
        matched_user_genes=["COL1A1"],
        number_of_user_genes_found=1,
        evidence_snippet="COL1A1 expression is elevated in myofibroblasts during fibrotic remodeling.",
        match_strength="Low",
        publication_year=2021,
    )

    extraction = ExtractionResult(
        evidence_rows=daf_rows + [myo_row],
        excluded_paper_count=2,
        audit_notes=["Fixture extraction result"],
    )

    candidates = CandidateResult(
        candidate_labels=[
            CandidateLabel(
                candidate_label="disease-associated fibroblast",
                normalized_label="disease-associated fibroblast",
                original_paper_labels=["disease-associated fibroblast"],
                supporting_genes=["COL1A1", "COL3A1", "POSTN"],
                supporting_pmids=["10001", "10002", "10003"],
                supporting_paper_count=3,
                candidate_score=0.82,
                specificity="intermediate",
                candidate_rationale=(
                    "3 evidence rows from 3 papers; genes: COL1A1, COL3A1, POSTN"
                ),
            ),
            CandidateLabel(
                candidate_label="myofibroblast",
                normalized_label="myofibroblast",
                original_paper_labels=["myofibroblast"],
                supporting_genes=["COL1A1"],
                supporting_pmids=["20001"],
                supporting_paper_count=1,
                candidate_score=0.41,
                specificity="specific",
                candidate_rationale="1 evidence row from 1 paper; genes: COL1A1",
            ),
        ],
        audit_notes=["Fixture candidates"],
    )

    da = DevilsAdvocateResult(
        critique_summary=(
            "Evidence strongly supports disease-associated fibroblast identity. "
            "Multiple markers co-expressed across independent papers."
        ),
        strongest_counterargument=(
            "COL1A1 alone could be consistent with myofibroblast; "
            "additional markers like ACTA2 would help distinguish."
        ),
        recommended_confidence_adjustment="keep",
        confidence_adjustment_reason="Multiple high-strength markers support DAF classification",
        nonspecific_genes=[],
        audit_notes=["Fixture DA"],
    )

    consensus = ConsensusResult(
        consensus_label="disease-associated fibroblast",
        confidence="Medium",
        consensus_rationale=(
            "Top candidate 'disease-associated fibroblast' supported by 3 paper(s) "
            "with genes: COL1A1, COL3A1, POSTN. Candidate score: 0.820. "
            "DA adjustment: keep → Medium confidence."
        ),
        supporting_genes=["COL1A1", "COL3A1", "POSTN"],
        main_supporting_pmids=["10001", "10002", "10003"],
        main_supporting_papers=[
            "10001: IPF single-cell fibrosis atlas study 1",
            "10002: IPF single-cell fibrosis atlas study 2",
            "10003: IPF single-cell fibrosis atlas study 3",
        ],
        audit_notes=["Fixture consensus"],
    )

    return AnalysisState(
        input=inp,
        extraction=extraction,
        candidates=candidates,
        devils_advocate=da,
        consensus=consensus,
    )


def make_at2_state() -> AnalysisState:
    """AT2 epithelial cell scenario."""
    inp = AnalysisInput(
        markers=["SFTPC", "SFTPA1", "SFTPA2", "ABCA3"],
        tissue="lung",
    )

    at2_rows = [
        EvidenceRow(
            pmid=f"3000{i}",
            paper_title=f"Alveolar epithelial cell atlas paper {i}",
            celltype_label="alveolar type 2 cell",
            matched_user_genes=["SFTPC", "SFTPA1"],
            number_of_user_genes_found=2,
            evidence_snippet=(
                "SFTPC and SFTPA1 are canonical markers of alveolar type 2 cells "
                "in healthy and fibrotic human lung."
            ),
            match_strength="High",
            publication_year=2023,
            tissue="lung",
        )
        for i in range(1, 3)
    ]

    extraction = ExtractionResult(
        evidence_rows=at2_rows,
        excluded_paper_count=1,
        audit_notes=["Fixture AT2 extraction"],
    )

    candidates = CandidateResult(
        candidate_labels=[
            CandidateLabel(
                candidate_label="alveolar type 2 cell",
                normalized_label="alveolar type 2 cell",
                original_paper_labels=["alveolar type 2 cell"],
                supporting_genes=["SFTPA1", "SFTPC"],
                supporting_pmids=["30001", "30002"],
                supporting_paper_count=2,
                candidate_score=0.65,
                specificity="specific",
                candidate_rationale="2 evidence rows from 2 papers; genes: SFTPA1, SFTPC",
            ),
        ],
        audit_notes=["Fixture AT2 candidates"],
    )

    da = DevilsAdvocateResult(
        critique_summary="SFTPC and SFTPA1 are well-established AT2 markers.",
        strongest_counterargument="SFTPC can be expressed in club cells in some contexts.",
        recommended_confidence_adjustment="keep",
        confidence_adjustment_reason="Strong canonical markers support AT2 classification",
        audit_notes=["Fixture AT2 DA"],
    )

    consensus = ConsensusResult(
        consensus_label="alveolar type 2 cell",
        confidence="High",
        consensus_rationale="Canonical AT2 markers SFTPC and SFTPA1 consistently identified.",
        supporting_genes=["SFTPA1", "SFTPC"],
        main_supporting_pmids=["30001", "30002"],
        audit_notes=["Fixture AT2 consensus"],
    )

    return AnalysisState(
        input=inp,
        extraction=extraction,
        candidates=candidates,
        devils_advocate=da,
        consensus=consensus,
    )


def make_weak_evidence_state() -> AnalysisState:
    """Housekeeping gene scenario — should give Insufficient."""
    inp = AnalysisInput(
        markers=["ACTB", "GAPDH", "MALAT1"],
    )

    extraction = ExtractionResult(
        evidence_rows=[],
        excluded_paper_count=5,
        audit_notes=["All papers excluded — no cell-type associations found for housekeeping genes"],
    )

    candidates = CandidateResult(
        candidate_labels=[],
        audit_notes=["No evidence rows to group into candidates"],
    )

    da = DevilsAdvocateResult(
        critique_summary=(
            "Genes ACTB, GAPDH, MALAT1 are non-specific housekeeping genes "
            "present in virtually all cell types."
        ),
        strongest_counterargument=(
            "These genes cannot distinguish cell types and should not drive annotation."
        ),
        recommended_confidence_adjustment="lower",
        confidence_adjustment_reason="All markers are housekeeping genes",
        nonspecific_genes=["ACTB", "GAPDH", "MALAT1"],
        audit_notes=["Fixture weak DA"],
    )

    consensus = ConsensusResult(
        consensus_label="Insufficient evidence",
        confidence="Insufficient",
        consensus_rationale=(
            "No candidate cell types could be extracted. "
            "All provided genes (ACTB, GAPDH, MALAT1) are non-specific housekeeping genes."
        ),
        what_would_improve_confidence=[
            "Provide more specific marker genes",
            "Add tissue or disease context",
            "Ensure genes are cell-type specific, not housekeeping",
        ],
        audit_notes=["Fixture weak consensus"],
    )

    return AnalysisState(
        input=inp,
        extraction=extraction,
        candidates=candidates,
        devils_advocate=da,
        consensus=consensus,
    )


def make_empty_state() -> AnalysisState:
    """No evidence retrieved at all."""
    inp = AnalysisInput(
        markers=["UNKNOWN1", "UNKNOWN2"],
    )

    return AnalysisState(
        input=inp,
        retrieval=RetrievalResult(
            retrieved_papers=[],
            total_searched=0,
            total_after_dedup=0,
            audit_notes=["No papers found for provided gene symbols"],
        ),
        extraction=None,
        candidates=None,
        devils_advocate=None,
        consensus=None,
    )
