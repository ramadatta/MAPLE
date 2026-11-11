"""Tests for counting independent evidence: reviews, datasets, labs, citations."""
from maples.independence import (
    annotate_independence,
    find_accessions,
    is_preprint,
    lab_key,
    paper_kind,
    quote_citations,
)
from maples.models import EvidenceRow, RetrievedPaper


def test_paper_kind_from_types_and_title():
    assert paper_kind(["Journal Article", "Review"]) == "review"
    assert paper_kind(["review"]) == "review"                       # OpenAlex
    assert paper_kind(["Journal Article"]) == "primary"
    assert paper_kind(["Comment", "Editorial"]) == "commentary"
    assert paper_kind([], "Basal cells in lung disease: a review") == "review"
    assert paper_kind(["Journal Article"], "A review of basal cells") == "primary"  # types win


def test_preprint_detection():
    assert is_preprint(["Preprint"])
    assert is_preprint([], doi="10.1101/2024.12.10.627749")
    assert not is_preprint(["Journal Article"], doi="10.1038/s41586-020-1234-5")


def test_find_accessions():
    text = ("Data are in GEO (GSE135893, GSE136831) and ArrayExpress E-MTAB-6149; raw reads "
            "under PRJNA123456 and EGAS00001004344. GSE135893 again.")
    assert find_accessions(text) == ["GSE135893", "GSE136831", "E-MTAB-6149", "PRJNA123456", "EGAS00001004344"]
    assert find_accessions("no data here GSE12") == []


def test_lab_key_formats():
    assert lab_key(["A B", "Jane Q Smith"]) == "smith j"
    assert lab_key(["Lee K", "Smith JQ"]) == "smith j"            # Europe PMC style
    assert lab_key(["José Müller"]) == "muller j"
    assert lab_key([]) == ""


def test_quote_citations():
    assert quote_citations("as described (Adams et al., 2020) and Habermann et al. 2020b") == [
        "Adams et al., 2020", "Habermann et al., 2020"]
    assert quote_citations("KRT17+ cells [12, 14] expand") == ["reference [12, 14]"]
    assert quote_citations("KRT17+ cells expand") == []


def _paper(pmid, authors, types=("Journal Article",), text=""):
    return RetrievedPaper(pmid=pmid, title=f"paper {pmid}", authors=list(authors),
                          publication_types=list(types), full_text=text)


def _row(pmid, snippet="KRT17+ cells"):
    return EvidenceRow(pmid=pmid, paper_title=f"paper {pmid}", celltype_label="basal",
                       matched_user_genes=["KRT17"], number_of_user_genes_found=1,
                       evidence_snippet=snippet)


def test_shared_dataset_or_lab_counts_once_and_reviews_never():
    papers = [
        _paper("1", ["X", "Ann Lee"], text="Data: GSE1000"),
        _paper("2", ["Y", "Bo Chen"], text="We reanalysed GSE1000"),     # same dataset as 1
        _paper("3", ["Z", "Ann Lee"]),                                    # same lab as 1
        _paper("4", ["W", "Cy Diaz"], text="E-MTAB-1"),                   # independent
        _paper("5", ["V", "Di Eve"], types=["Review"]),                   # review
    ]
    rows = [_row(p.pmid) for p in papers] + [_row("1", "again (Lee et al., 2019)")]
    summary = annotate_independence(rows, papers)
    assert summary["primary_papers"] == 4
    assert summary["reviews"] == 1
    assert summary["independent_sources"] == 2
    assert rows[0].source_id == rows[1].source_id == rows[2].source_id != rows[3].source_id
    assert rows[4].paper_kind == "review" and rows[4].source_id == ""
    assert rows[5].cites == ["Lee et al., 2019"]
    assert summary["datasets"] == 2 and summary["labs"] == 3   # papers 1 and 3 share a lab
