# MAPLE — Marker-based Annotation with PubMed Literature Evidence

**Annotate single-cell marker genes with PubMed-backed cell-type evidence.**

Paste your marker genes and MAPLE will:

1. Search PubMed for papers describing those genes
2. Detect every cell type named in each paper
3. Show which of your genes each paper attributes to which cell type
4. Rank papers by how specifically they link your markers to a named population

---

### Quick start — just paste your genes

```
TP63, KRT17, LAMB3, LAMC2, VIM, CDH2, FN1, COL1A1, TNC, MMP7
```

### Add optional context for more targeted results

```
Genes: COL1A1, COL3A1, POSTN, CTHRC1, DCN, LUM
Tissue: lung
Species: Human
Disease: pulmonary fibrosis
```

**Tissue, Species, and Disease are all optional.** When omitted MAPLE searches all available literature.

---

### Understanding the output table

| Column | Meaning |
|---|---|
| **Cell Type** | Population named in that paper |
| **Attributed genes** | Your input genes found near the cell-type mention |
| **Attr/Found** | Attributed genes / total input genes found in paper |

One paper can appear as multiple rows if it describes more than one cell type from your gene set.
