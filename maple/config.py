"""Runtime configuration for MAPLE."""
import os

PAPERS_PER_GENE = int(os.getenv("MAPLE_PAPERS_PER_GENE", "10"))
MAX_EVIDENCE_PAPERS = int(os.getenv("MAPLE_MAX_EVIDENCE_PAPERS", "150"))
ENABLE_FULLTEXT = os.getenv("MAPLE_ENABLE_FULLTEXT", "true").lower() not in ("0", "false", "no")
ENABLE_FULLTEXT_DISCOVERY = os.getenv("MAPLE_ENABLE_FULLTEXT_DISCOVERY", "true").lower() not in (
    "0", "false", "no"
)
FULLTEXT_DISCOVERY_MAX_PAPERS = int(os.getenv("MAPLE_FULLTEXT_DISCOVERY_MAX_PAPERS", "12"))
ENABLE_LLM_EXTRACTION = os.getenv("MAPLE_ENABLE_LLM_EXTRACTION", "true").lower() not in ("0", "false", "no")
LLM_EXTRACTION_MAX_PAPERS = int(os.getenv("MAPLE_LLM_EXTRACTION_MAX_PAPERS", "25"))
# Per-paper LLM extraction calls run concurrently in a bounded thread pool.
# This is the single biggest latency lever (was fully serial). Raise if your
# LLM provider tolerates more parallel requests; lower if you hit rate limits.
EVIDENCE_EXTRACTION_CONCURRENCY = int(os.getenv("MAPLE_EVIDENCE_EXTRACTION_CONCURRENCY", "12"))
# Full-text fetches (PMC/EPMC/preprint) also run concurrently.
FULLTEXT_FETCH_CONCURRENCY = int(os.getenv("MAPLE_FULLTEXT_FETCH_CONCURRENCY", "12"))
# Characters of PMC full text handed to the LLM extractor per paper.
LLM_FULLTEXT_CHARS = int(os.getenv("MAPLE_LLM_FULLTEXT_CHARS", "14000"))
TABLE_PAGE_SIZE = int(os.getenv("MAPLE_TABLE_PAGE_SIZE", "25"))

# --- Preprint (bioRxiv/medRxiv) full-text source ---
ENABLE_PREPRINTS = os.getenv("MAPLE_ENABLE_PREPRINTS", "true").lower() not in ("0", "false", "no")
PREPRINT_DISCOVERY_MAX_PAPERS = int(os.getenv("MAPLE_PREPRINT_DISCOVERY_MAX_PAPERS", "8"))

# --- OpenAlex full-text discovery (quota-free finder) ---
ENABLE_OPENALEX = os.getenv("MAPLE_ENABLE_OPENALEX", "true").lower() not in ("0", "false", "no")
OPENALEX_MAX_PAPERS = int(os.getenv("MAPLE_OPENALEX_MAX_PAPERS", "15"))
OPENALEX_MAILTO = os.getenv("MAPLE_OPENALEX_MAILTO", "") or os.getenv("NCBI_EMAIL", "") or "maple@example.org"

# --- Google Scholar discovery via Smithery MCP (full-text-aware finder) ---
# Finds papers whose markers appear only in the body text (missed by abstract search).
ENABLE_SCHOLAR = os.getenv("MAPLE_ENABLE_SCHOLAR", "false").lower() in ("1", "true", "yes")
SMITHERY_SCHOLAR_URL = os.getenv(
    "MAPLE_SMITHERY_SCHOLAR_URL", "https://server.smithery.ai/google/scholar/mcp"
)
SMITHERY_API_KEY = os.getenv("SMITHERY_API_KEY", "").strip()
SCHOLAR_MAX_PAPERS = int(os.getenv("MAPLE_SCHOLAR_MAX_PAPERS", "20"))
SCHOLAR_TIMEOUT_SECONDS = int(os.getenv("MAPLE_SCHOLAR_TIMEOUT_SECONDS", "30"))
# When false (default), tissue/disease fields are ignored for PubMed queries and ranking.
USE_USER_CONTEXT = os.getenv("MAPLE_USE_USER_CONTEXT", "false").lower() in ("1", "true", "yes")
PUBTATOR_ENABLED = os.getenv("MAPLE_PUBTATOR_ENABLED", "false").lower() in ("1", "true", "yes")
BIOMEDICAL_ENCODER_ENABLED = os.getenv("MAPLE_BIOMEDICAL_ENCODER_ENABLED", "false").lower() in (
    "1", "true", "yes"
)
BIOMEDICAL_ENCODER_MODEL = os.getenv("MAPLE_BIOMEDICAL_ENCODER_MODEL", "").strip()
