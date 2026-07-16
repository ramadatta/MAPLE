---
title: MAPLE
emoji: 🍁
colorFrom: red
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---


<p align="center">
  <img src="maple-logo.svg" alt="MAPLE logo" width="400" height="400">
</p>

<p align="center">
  A research copilot that tells you <em>which cell types the published literature already
  annotates your marker genes to</em> — with every claim traced to an exact, searchable
  quote from a real paper.
</p>

<p align="center">
  <a href="https://huggingface.co/spaces/ramadatta88/MAPLE"><strong>🚀 Try the live demo on Hugging Face Spaces</strong></a>
</p>

<p align="center">
  <a href="https://huggingface.co/spaces/ramadatta88/MAPLE">
    <img src="https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Live%20Demo-blue" alt="Hugging Face Space">
  </a>
</p>

<p align="center"><em>Research use only. Not a clinical or diagnostic tool.</em></p>

---

## What MAPLE does

You give MAPLE a set of marker genes. It searches the literature, reads the papers, and
returns an **evidence table**: each row is a paper that annotates some of your genes to a
named cell type, with the exact supporting sentence(s), the PMID, and the tissue/disease
context. A consensus label and a skeptical "devil's advocate" critique summarise the table.

The cell-type label always comes from the retrieved papers — never from hard-coded marker
rules or the model's background knowledge.

## How it works

MAPLE runs a five-stage pipeline:

1. **Retrieval** — PubMed search, enriched with open full text and full-text-aware discovery.
2. **Evidence extraction** — the LLM reads each paper and decides whether your genes are
   described as markers of a specific cell type, quoting the exact text.
3. **Candidate grouping** — evidence rows are grouped and scored into candidate labels.
4. **Devil's advocate** — the leading candidate is challenged for specificity and conflicts.
5. **Consensus** — a final, evidence-based label with transparent reasoning.

### Literature sources (all free, no key required)

| Source | Role |
|---|---|
| PubMed (NCBI E-utilities) | Primary retrieval |
| PMC / Europe PMC | Open-access full text |
| **OpenAlex** (full-text search) | Finds papers whose markers appear only in the body |
| **bioRxiv / medRxiv** | Reads the open preprint when the publisher copy is paywalled |
| Google Scholar (via Smithery MCP) | Optional finder; off by default (quota-limited) |

## Scientific guardrails

- **LLM-first, literature-grounded** — labels are extracted from paper text, not marker priors.
- **Exact, searchable quotes** — every evidence snippet is copied verbatim (fragments joined
  with `…`) so you can paste it into the source paper and find it.
- **No fabricated citations** — PMIDs and titles come directly from NCBI; genes must appear
  in the quoted passage or they are dropped.
- **Conservative** — reports "Multiple cell types" / "Insufficient" when the literature is
  genuinely ambiguous, rather than forcing a single label.

## Quick start (local)

```bash
git clone https://github.com/<your-user>/MAPLE.git
cd MAPLE
pip install -r requirements.txt

cp .env.example .env      # add your LLM key (see Configuration)
chainlit run app.py       # open http://localhost:8000
```

Paste marker genes, e.g.:

```
TP63, CDH2, CDKN1A, CDKN2A, KRT17, VIM
COL1A1, COL3A1, POSTN, DCN
```

## Programmatic use (Python API)

MAPLE can be called directly from a script or notebook — no UI required. The LLM
client is built automatically from your environment (`OPENAI_API_KEY` /
`GITLAB_TOKEN`); without a key it runs in degraded mode (retrieval only).

```python
from maple import annotate

result = annotate(
    markers=["COL1A1", "COL3A1", "POSTN", "CTHRC1"],
    tissue="lung",
    disease="idiopathic pulmonary fibrosis",
    species="human",
)

print(result.label, result.confidence)          # e.g. "fibroblast" High
for row in result.evidence:
    print(row.number_of_user_genes_found, row.celltype_label, row.pmid)

print(result.run_metadata.model)                 # provenance for reproducibility
result.to_json()                                 # stable, serializable output
```

Annotate many marker sets (e.g. one per cluster) in one call — failures are
isolated per group:

```python
from maple import annotate_marker_sets

results = annotate_marker_sets(
    {
        "0": ["COL1A1", "DCN", "LUM"],
        "1": ["SFTPC", "ABCA3", "SFTPA1"],
    },
    tissue="lung",
    species="human",
)
for cluster, res in results.items():
    print(cluster, "->", res.label, res.confidence)
```

In async contexts (FastAPI, notebooks) use the coroutine forms
`annotate_async(...)` / `annotate_marker_sets_async(...)`. A Scanpy/`AnnData`
adapter (`annotate_clusters(adata, groupby=...)`) is planned next.

## Deploy (free, public)

MAPLE ships with a Hugging Face Spaces–ready Docker setup. See **[DEPLOY_HF.md](DEPLOY_HF.md)**
for the step-by-step (create a Docker Space, push this repo, paste secrets). Your LLM key
lives in the Space's encrypted secrets — never in the repo.

## Configuration

Set these in `.env` (local) or as Space secrets (deploy).

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | required | LLM key (Blablador, OpenAI, or GitLab token) |
| `OPENAI_BASE_URL` | OpenAI default | Override for Blablador / compatible endpoints |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Model name |
| `USE_ENV_TOKEN_ONLY` | false | Use only the server key; disallow pasting in chat |
| `MAPLE_PAPERS_PER_GENE` | 5 | PubMed papers fetched per gene |
| `MAPLE_ENABLE_FULLTEXT` | true | Fetch PMC full text when available |
| `MAPLE_ENABLE_LLM_EXTRACTION` | true | Use the LLM for evidence extraction |
| `MAPLE_ENABLE_OPENALEX` | true | Quota-free OpenAlex full-text finder |
| `MAPLE_ENABLE_PREPRINTS` | true | Read open bioRxiv/medRxiv full text |
| `MAPLE_OPENALEX_MAILTO` | `NCBI_EMAIL` | Contact email for OpenAlex's polite pool |
| `MAPLE_ENABLE_SCHOLAR` | false | Google Scholar finder via Smithery (needs `SMITHERY_API_KEY`) |
| `MAPLE_TABLE_PAGE_SIZE` | 20 | Rows per evidence-table page |

## Development

```bash
python -m pytest tests/ -v          # test suite
python -m scripts.run_evals         # evaluations (heuristic)
```

## Project structure

```
app.py                 Chainlit entry point
maple/                 Main package
  models.py            Pydantic data models
  input_parser.py      Gene + context detection
  config.py            Runtime configuration
  runtime/             Five-stage agent pipeline
  literature/          PubMed / PMC / OpenAlex / preprint adapters
  extraction/          Prompts, schemas, validators
  ui/                  Chainlit UI components
services/              API clients (PubMed, PMC, LLM, OpenAlex, preprints, Scholar)
utils/                 Parsing, scoring, UI helpers
tests/                 Automated tests
DEPLOY_HF.md           Hugging Face Spaces deployment guide
```

## Author

**Sai Rama Sridatta Prakki** — Doctoral Candidate, Helmholtz Munich.

## Disclaimer

A research tool, not a clinical or diagnostic one. All annotations require expert review and
verification against the original PMIDs before any publication or decision.

## License

See the [LICENSE](LICENSE) file.
