---
title: MAPLES
emoji: 🍁
colorFrom: red
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---


<p align="center">
  <img src="maple-logo.svg" alt="MAPLES logo" width="400" height="400">
</p>

<h3 align="center">MAPLES</h3>
<p align="center"><em>Mapping Analogous Programs from Literature Evidence across Species, organs and diseases</em></p>

<p align="center">
  MAPLES tells you what your own field already knows about your genes, then what other
  fields know that yours doesn't yet. Every claim comes with an exact quote.
</p>

<p align="center">
  <a href="https://huggingface.co/spaces/ramadatta88/MAPLE"><strong>🚀 Try the live demo on Hugging Face Spaces</strong></a>
</p>

<p align="center"><em>Research use only. Not a clinical or diagnostic tool.</em></p>

---

## What MAPLES does

You give MAPLES a set of marker genes and, optionally, your own field (organ, disease,
species). It searches the literature, reads the papers, and returns an **evidence table**:
each row is a paper that links some of your genes to a named cell type or cell state, with
the exact supporting sentence(s), the PMID, and the paper's own tissue, disease and species.
A summary and a skeptical "devil's advocate" critique sit on top of the table.

The local app shows the results in six tabs: a summary (how independent the evidence is,
where it comes from, different names for the same population), a **field map** (your field
in the centre, near and far fields around it; or your genes in the centre when no field is
given), a **gene × field** dot plot, a **body map** with a year slider, a **timeline**, and a
filterable evidence table. Every dot opens the exact quotes behind it.

Cell-type labels always come from the retrieved papers, never from hard-coded marker rules
or the model's background knowledge.

## Quick start: run it on your own computer

```bash
git clone <this repository> maples
cd maples
pip install -e .          # installs the `maples` command

maples ui                 # opens MAPLES in your browser
```

The first time, MAPLES asks for an AI key under **Settings**. Any OpenAI-compatible service
works: OpenAI, Blablador (Helmholtz), or a free local model through Ollama. The key is saved
only on your computer, in `~/.maples/config.json` (readable only by you).

The app runs locally at `http://127.0.0.1:8765`. Your genes go only to the literature
databases and the AI service you choose.

### From the terminal

```bash
maples run TP63 KRT17 CDKN2A --tissue lung --disease "pulmonary fibrosis" --json result.json
maples config --set OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-4.1-mini
maples config             # show what is set and where it is stored
```

### From Python

```python
from maples import annotate

result = annotate(
    markers=["COL1A1", "COL3A1", "POSTN", "CTHRC1"],
    tissue="lung",
    disease="idiopathic pulmonary fibrosis",
    species="human",
)

print(result.label, result.confidence)
for row in result.evidence:
    print(row.number_of_user_genes_found, row.celltype_label, row.pmid)

print(result.run_metadata.model)   # provenance for reproducibility
result.to_json()                   # stable, serializable output
```

Annotate many marker sets (e.g. one per cluster) in one call. Failures are isolated per group:

```python
from maples import annotate_marker_sets

results = annotate_marker_sets(
    {"0": ["COL1A1", "DCN", "LUM"], "1": ["SFTPC", "ABCA3", "SFTPA1"]},
    tissue="lung",
    species="human",
)
for cluster, res in results.items():
    print(cluster, "->", res.label, res.confidence)
```

In async code use `annotate_async(...)` / `annotate_marker_sets_async(...)`.

## How it works

MAPLES runs a five-stage pipeline:

1. **Retrieval**: PubMed search, enriched with open full text and full-text-aware discovery.
2. **Evidence extraction**: the AI reads each paper and decides whether your genes are
   described as markers of a specific cell type, quoting the exact text.
3. **Candidate grouping**: evidence rows are grouped and scored into candidate labels.
4. **Devil's advocate**: the leading candidate is challenged for specificity and conflicts.
5. **Consensus**: a summary label with transparent reasoning.

### Literature sources (all free)

| Source | Role |
|---|---|
| PubMed (NCBI E-utilities) | Primary retrieval |
| PMC / Europe PMC | Open-access full text |
| OpenAlex (full-text search) | Finds papers whose markers appear only in the body |
| bioRxiv / medRxiv | Reads the open preprint when the publisher copy is paywalled |
| Google Scholar (via Smithery MCP) | Optional finder; off by default (`pip install -e ".[scholar]"`) |

## Scientific guardrails

- **Literature-grounded**: labels are extracted from paper text, not marker priors.
- **Exact, searchable quotes**: every snippet is copied verbatim (fragments joined with `…`)
  and checked against the paper text, so you can find it with Ctrl+F.
- **No fabricated citations**: PMIDs and titles come directly from NCBI; genes must appear
  in the quoted passage or they are dropped.
- **Conservative**: reports "Multiple cell types" / "Insufficient" when the literature is
  genuinely ambiguous, rather than forcing a single label.

## Configuration

Settings can come from the app's Settings screen, `maples config --set`, environment
variables, or a `.env` file in the folder you start MAPLES from. Environment variables win.

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | required | AI key (OpenAI, Blablador, or `ollama` for a local model) |
| `OPENAI_BASE_URL` | OpenAI | Endpoint for Blablador, Ollama or another compatible service |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Model name |
| `NCBI_EMAIL` / `NCBI_API_KEY` | none | Recommended; raises PubMed rate limits |
| `MAPLES_HOME` | `~/.maples` | Where settings and the cache live |
| `MAPLE_PAPERS_PER_GENE` | 10 | PubMed papers fetched per gene |
| `MAPLE_ENABLE_FULLTEXT` | true | Fetch PMC full text when available |
| `MAPLE_ENABLE_OPENALEX` | true | Quota-free OpenAlex full-text finder |
| `MAPLE_ENABLE_PREPRINTS` | true | Read open bioRxiv/medRxiv full text |
| `MAPLE_ENABLE_SCHOLAR` | false | Google Scholar finder via Smithery (needs `SMITHERY_API_KEY`) |

## Hugging Face demo

The public demo still uses the older Chainlit chat app (`app.py`). See
**[DEPLOY_HF.md](DEPLOY_HF.md)**. Locally: `pip install -e ".[demo]"` then `chainlit run app.py`.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v          # test suite
python -m scripts.run_evals         # evaluations (heuristic)
```

## Project structure

```
maples/                MAPLES package
  api.py               Python API (annotate, annotate_marker_sets)
  cli.py               `maples` command (ui, run, config)
  settings.py          Local settings in ~/.maples
  web/                 Local browser app (`maples ui`)
  runtime/             Five-stage pipeline
  literature/          PubMed / PMC / OpenAlex / preprint retrieval
  extraction/          Prompts, schemas, quote validators
  services/            API clients (PubMed, PMC, LLM, OpenAlex, preprints, Scholar) and cache
  utils/               Gene parsing and helpers
  ui/                  HTML pieces for the Chainlit demo
app.py                 Chainlit demo (Hugging Face)
tests/                 Automated tests
```

## Author

**Sai Rama Sridatta Prakki**, Doctoral Candidate, Helmholtz Munich.

## Disclaimer

A research tool, not a clinical or diagnostic one. All annotations require expert review and
verification against the original papers before any publication or decision.

## License

See the [LICENSE](LICENSE) file.
