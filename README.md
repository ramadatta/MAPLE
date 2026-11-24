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
  <img src="maples/web/static/leaf.svg" alt="MAPLES maple leaf" width="160" height="160">
</p>

<h1 align="center">MAPLES</h1>
<p align="center"><em>Mapping Analogous Programs from Literature Evidence across Species, organs and diseases</em></p>

<p align="center">
  Give MAPLES your marker genes. It reads the literature and tells you which cell types
  and states papers link them to: first in your own field, then in other organs, diseases
  and species. Every claim comes with an exact quote from the paper.
</p>

<p align="center">
  <a href="https://huggingface.co/spaces/ramadatta88/MAPLE"><strong>Try the live demo on Hugging Face</strong></a>
  ·
  <a href="#run-it-on-your-own-computer"><strong>Install it</strong></a>
</p>

<p align="center"><em>Research use only. Not a clinical or diagnostic tool.</em></p>

---

## What you get

You paste a gene list (for example, the markers of one single-cell cluster) and, if you
like, your own field: organ, disease, species. A run takes **3–6 minutes** and gives you:

- **Summary**: the cell populations the papers describe, how many *independent* sources
  support them (reviews, and papers that reuse the same dataset or come from the same lab,
  count once), different names for the same population, and a "devil's advocate" critique.
- **Evidence table**: one row per quote, with the paper, year, field and dataset accession.
  Sort by any column, filter by gene, field or evidence type, and download as CSV or JSON.
- **Field map**: your field in the centre, near fields (same organ) and far fields (other
  organs) around it, with lines for the genes they share. Without a field, your genes sit in
  the centre and fields are placed by how many of your genes they explain.
- **Gene × field** dot plot and a **body map** of where the evidence comes from.

Click any row or dot to read the exact sentence(s) from the paper, with links to PubMed,
the full text and the DOI.

## How it differs from asking a chatbot

| | A general chatbot | MAPLES |
|---|---|---|
| Where facts come from | Mostly what the model memorised in training | Papers fetched from PubMed and Europe PMC during the run |
| Role of the AI model | Library and writer | Only a reader of the papers it is handed |
| Citations | Can be invented or point to the wrong paper | PMID, title and year come from NCBI, never from the model |
| Proof | A paragraph to trust | The exact sentence, which you can find with Ctrl+F in the paper |
| Counting evidence | "Many studies show…" | Independent sources, with reviews and shared datasets counted once |
| Unclear evidence | Usually one confident answer | Says "Multiple cell types" or "Insufficient" |

## How MAPLES chooses what to read

MAPLES does not read every paper that mentions your genes (for a 10-gene panel that can be
over 16,000). It combines three kinds of search:

1. **Regular PubMed searches** for each gene, gene pairs and cell-type words; the best hits
   are ranked by how clearly they tie your genes to a cell type, and the top 25 are read.
2. **The single-cell pool**: *every* PubMed paper whose title or abstract names **2 or more
   of your genes together with single-cell words**. Other names of one gene count once
   (VEGFR3 and FLT4 are the same gene). These are read in their own 40 slots; when there are
   more, an AI skim of the abstracts picks the ones that tie the genes to a cell type.
3. **Full-text discovery** through Europe PMC, OpenAlex and bioRxiv/medRxiv, for papers
   that name your markers only in the body text, not in the abstract.

The summary lists every single-cell pool paper and what happened to it (quoted, read
without a quote, screened out, or not read because the slots were full), so you can see
what MAPLES covered and what it did not.

**What MAPLES cannot tell you:** which paper *first* described a gene. Genes were often first
described under other names, older papers are rarely open, and MAPLES reads a selection
that favours studies naming a cell type.

## How far to trust the results

- **Checked:** every paper and PMID comes from PubMed; every quote is matched against the
  paper's text; the cell-type name must appear in the paper, and a gene is only listed for
  a row if it appears in that row's quote (rows left with no gene are dropped). Nothing is
  invented.
- **Not checked:** an AI model decides what each quote means and which names to group. It can
  misread a sentence (for example, miss a "not"). Read the quote before you rely on a row.
- **Not complete:** paywalled papers are read from the abstract only. No quote does not mean
  no evidence.

## Run it on your own computer

```bash
git clone https://github.com/ramadatta/MAPLES.git
cd MAPLES
pip install -e .          # Python 3.9+; installs the `maples` command

maples ui                 # opens MAPLES in your browser at http://127.0.0.1:8765
```

The first time, MAPLES asks for an AI service under **Settings**. Any OpenAI-compatible
service works:

| Service | What you need |
|---|---|
| OpenAI | An API key |
| Blablador (Helmholtz) | A free key for Helmholtz members |
| A local model through [Ollama](https://ollama.com) | Nothing: no key, and no gene list sent to an AI company |

The key is saved only on your computer, in `~/.maples/config.json` (readable only by you).
Your genes go to the public literature databases (as search terms, like typing them into
PubMed) and to the AI service you choose. The model's training date does not matter, because
MAPLES fetches current papers and only asks the model to read them.

While a run is going, the page shows each step, the Europe PMC searches as they come back,
and quotes as they are found; the browser tab shows the elapsed time and a ✓ when the
results are ready.

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

Annotate many marker sets (for example, one per cluster) in one call. Failures are isolated
per group:

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

In async code use `annotate_async(...)` and `annotate_marker_sets_async(...)`.

## Tips for gene lists

- Paste symbols separated by commas, spaces or new lines; words like "and" are ignored.
- Protein names work too: VEGFR2 also finds papers that say KDR.
- Put your strongest markers first: the first genes drive the full-text searches.
- Very long lists still work, but the reading budget is shared between all genes.

## Literature sources (all free)

| Source | Role |
|---|---|
| PubMed (NCBI E-utilities) | Regular searches and the single-cell pool |
| PMC / Europe PMC | Open-access full text and full-text search |
| OpenAlex | Finds papers whose markers appear only in the body text |
| bioRxiv / medRxiv | Reads the open preprint when the publisher copy is paywalled |
| Google Scholar (via Smithery) | Optional finder, off by default (`pip install -e ".[scholar]"`) |

Europe PMC full-text search can be slow (10–40 seconds per search on a busy day). MAPLES
runs these searches alongside PubMed, waits for them, and retries when the service is busy,
because they find papers the other sources miss.

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
| `MAPLE_LLM_EXTRACTION_MAX_PAPERS` | 25 | Papers read from the regular searches |
| `MAPLE_ENABLE_SC_POOL` | true | Fetch the single-cell pool |
| `MAPLE_SC_POOL_READ_MAX` | 40 | Reading slots for single-cell pool papers |
| `MAPLE_DISCOVERY_BUDGET_SECONDS` | 240 | How long Europe PMC searches may take before MAPLES moves on |
| `MAPLE_ENABLE_FULLTEXT` | true | Fetch PMC full text when available |
| `MAPLE_ENABLE_OPENALEX` | true | OpenAlex full-text finder |
| `MAPLE_ENABLE_PREPRINTS` | true | Read open bioRxiv/medRxiv full text |
| `MAPLE_ENABLE_SCHOLAR` | false | Google Scholar finder via Smithery (needs `SMITHERY_API_KEY`) |

Setting names that start with `MAPLE_` keep their original prefix so existing setups keep
working.

## Hosting it for others

The `Dockerfile` runs MAPLES as a website (this is how the Hugging Face demo runs). In
**hosted mode** (`MAPLES_HOSTED=true`) the AI key and service come only from the host's
environment, the Settings screen is hidden, visitors cannot read or redirect the key, and at
most `MAPLES_MAX_HOSTED_RUNS` analyses (default 3) run at once. See
[DEPLOY_HF.md](DEPLOY_HF.md) for a step-by-step Hugging Face guide.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q                 # full test suite, offline, a few seconds
python -m scripts.run_evals         # evaluations (heuristic)
```

## Project structure

```
maples/                 MAPLES package
  api.py                Python API (annotate, annotate_marker_sets)
  cli.py                `maples` command (ui, run, config)
  settings.py           Local settings in ~/.maples; hosted mode
  web/                  Browser app (`maples ui`)
  runtime/              Pipeline: retrieval, evidence, labels, candidates, critique, consensus
  literature/           PubMed, single-cell pool, PMC, OpenAlex and preprint retrieval
  extraction/           Prompts, schemas, quote validators
  services/             API clients (PubMed, PMC, Europe PMC, LLM, OpenAlex, preprints) and cache
  genes.py              Gene names and aliases (VEGFR3 = FLT4)
  fields.py             Your field, near fields and far fields
  independence.py       Counting independent sources
tests/                  Automated tests (offline)
app.py                  Older Chainlit chat demo
```

## Author

**Sai Rama Sridatta Prakki**, Doctoral Candidate, Helmholtz Munich.

## Disclaimer

A research tool, not a clinical or diagnostic one. All results need expert review and
checking against the original papers before any publication or decision.

## License

MIT. See the [LICENSE](LICENSE) file.
