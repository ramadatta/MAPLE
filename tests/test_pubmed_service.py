"""Tests for PubMed/NCBI retrieval service."""

from __future__ import annotations

import pytest

from services.pubmed_service import EUtilitiesBackend


@pytest.fixture
def pubmed_backend():
    """Create a PubMedService instance."""
    return EUtilitiesBackend()


class TestEUtilitiesBackendBasics:
    """Basic tests for NCBI E-utilities backend."""

    def test_backend_initializes(self, pubmed_backend):
        """Backend should initialize with default env vars."""
        assert pubmed_backend.email == "user@example.com"
        assert pubmed_backend.api_key == ""
        assert pubmed_backend.tool == "maple"

    def test_fetch_papers_handles_empty_pmid_list(self, pubmed_backend):
        """Fetch should handle empty PMID list."""
        papers = pubmed_backend.fetch_papers([])
        # Should return empty list
        assert papers == []
        assert isinstance(papers, list)

    def test_search_with_invalid_query_returns_empty(self, pubmed_backend):
        """Search with invalid query should handle gracefully."""
        # This will make a real API call, but with a query that returns nothing
        # Note: This test is integration-style; in production use mocks
        result = pubmed_backend.search("xyzabc123nonexistentgenexyzabc", max_results=1)
        assert isinstance(result, list)

    def test_parse_pubmed_xml_extracts_pmcid(self, pubmed_backend):
        """PMCID must be retained so the runtime can fetch PMC full text."""
        xml = """\
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>32832599</PMID>
              <Article>
                <ArticleTitle>Single-cell RNA-seq reveals ectopic populations</ArticleTitle>
                <Journal><Title>Science Advances</Title></Journal>
                <Abstract><AbstractText>Aberrant basaloid cells were identified.</AbstractText></Abstract>
                <JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue>
              </Article>
            </MedlineCitation>
            <PubmedData>
              <ArticleIdList>
                <ArticleId IdType="pubmed">32832599</ArticleId>
                <ArticleId IdType="pmc">PMC7473672</ArticleId>
              </ArticleIdList>
            </PubmedData>
          </PubmedArticle>
        </PubmedArticleSet>
        """

        papers = pubmed_backend._parse_pubmed_xml(xml)

        assert len(papers) == 1
        assert papers[0].pmcid == "PMC7473672"
        assert papers[0].is_open_access is True

    def test_parse_pubmed_xml_ignores_reference_pmcids(self, pubmed_backend):
        """Reference-list PMC IDs must not be assigned to the main article."""
        xml = """\
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>35355018</PMID>
              <Article>
                <ArticleTitle>Human distal lung maps and lineage hierarchies reveal a bipotent progenitor.</ArticleTitle>
                <Journal><Title>Nature</Title></Journal>
                <Abstract><AbstractText>AT0 cells are described.</AbstractText></Abstract>
                <JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue>
              </Article>
            </MedlineCitation>
            <PubmedData>
              <ReferenceList>
                <Reference>
                  <ArticleIdList>
                    <ArticleId IdType="pmc">PMC7889871</ArticleId>
                  </ArticleIdList>
                </Reference>
              </ReferenceList>
              <ArticleIdList>
                <ArticleId IdType="pubmed">35355018</ArticleId>
                <ArticleId IdType="pmc">PMC9169066</ArticleId>
              </ArticleIdList>
            </PubmedData>
          </PubmedArticle>
        </PubmedArticleSet>
        """

        papers = pubmed_backend._parse_pubmed_xml(xml)

        assert papers[0].pmcid == "PMC9169066"

    def test_clamping_score(self):
        """Test score clamping utility."""
        from utils.scoring import clamp_score

        assert clamp_score(0.5) == 0.5
        assert clamp_score(-0.5) == 0.0
        assert clamp_score(1.5) == 1.0
        assert clamp_score(0.0) == 0.0
        assert clamp_score(1.0) == 1.0
