"""Label agent: group the different names papers use for the same cell population.

Two passes:
1. Deterministic: formatting variants share a key ("KRT5 - /KRT17 + cells" and
   "KRT5-/KRT17+ cell" → "krt5-/krt17+").
2. One LLM call (optional) merges true synonyms among the remaining names, under
   conservative rules, choosing a name that one of the papers actually used.

Each evidence row keeps its own ``celltype_label`` (as the paper wrote it); the
shared name goes to ``label_group`` and drives ``normalized_label`` so candidate
scoring counts the population once.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Optional

from pydantic import BaseModel, Field

from maples.models import EvidenceRow, LabelGroup

logger = logging.getLogger(__name__)

_TRAILING_GENERIC = {
    "cell", "cells", "population", "populations", "subpopulation", "subpopulations",
    "subset", "subsets", "cluster", "clusters",
}
MAX_LLM_LABELS = 60


def label_key(label: str) -> str:
    """Formatting-insensitive key for a cell-type name."""
    s = (label or "").lower()
    for a, b in (("−", "-"), ("–", "-"), ("—", "-"), ("⁺", "+"), ("⁻", "-"), ("’", "'")):
        s = s.replace(a, b)
    s = re.sub(r"\s*\([^)]*\)", "", s)                      # drop parenthetical asides
    s = re.sub(r"\s+([+-])(?=\s|/|,|$)", r"\1", s)          # "krt5 +" -> "krt5+"
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)             # "basaloid-like" -> "basaloid like"
    s = re.sub(r"\btype\s+ii\b", "type 2", s)
    s = re.sub(r"\btype\s+i\b", "type 1", s)
    s = re.sub(r"[^a-z0-9+\-/' ]", " ", s)
    words = s.split()
    while len(words) > 1 and words[-1] in _TRAILING_GENERIC:
        words.pop()
    if words and len(words[-1]) > 4 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


class _MergeGroup(BaseModel):
    name: str = ""
    members: list[int] = Field(default_factory=list)


class _MergeOutput(BaseModel):
    groups: list[_MergeGroup] = Field(default_factory=list)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _display_name(labels: list[str]) -> str:
    """The name most papers use. Spelling variants count together ("Aberrant
    Basaloid cells" + "aberrant basaloid cells"); within the winning variant the
    most frequent spelling is shown. When names are equally common, the shortest
    (most general) wins, e.g. "basal cells" over "basal cells primed for secretory
    differentiation". Remaining ties go to the highest-ranked paper (rows arrive in
    relevance order; Counter keeps first-seen order and sorting is stable)."""
    by_key: dict[str, list[str]] = defaultdict(list)
    for label in labels:
        by_key[label_key(label) or label].append(label)
    best_key = sorted(by_key, key=lambda k: (-len(by_key[k]), len(k)))[0]
    spellings = Counter(by_key[best_key])
    return sorted(spellings, key=lambda name: -spellings[name])[0]


def _group_system(rows: list[EvidenceRow]) -> Optional[str]:
    """Most common organ system among a group's rows (None if none is stated)."""
    systems = Counter(r.field.organ_system for r in rows if r.field and r.field.organ_system)
    return systems.most_common(1)[0][0] if systems else None


def _split_by_system(gidx: list[int], systems: dict[int, Optional[str]]) -> list[list[int]]:
    """Keep a proposed merge only within one organ system. Groups with no stated
    system join the largest partition. Linking programs *across* organs is the
    field map's job, not the name grouping's."""
    parts: dict[str, list[int]] = defaultdict(list)
    unknown: list[int] = []
    for g in gidx:
        (parts[systems[g]] if systems[g] else unknown).append(g)
    if not parts:
        return [gidx]
    largest = max(parts, key=lambda k: len(parts[k]))
    parts[largest].extend(unknown)
    return [p for p in parts.values() if len(p) >= 2]


def _llm_merges(groups: list[list[int]], rows: list[EvidenceRow], llm) -> list[tuple[str, list[int]]]:
    """Ask the LLM which deterministic groups are synonyms. Returns (name, group indices)."""
    from maples.extraction.prompts import LABEL_MERGE_SYSTEM, LABEL_MERGE_USER_TEMPLATE

    order = sorted(range(len(groups)), key=lambda g: -len(groups[g]))[:MAX_LLM_LABELS]
    lines, line_to_group, names = [], {}, {}
    for line_no, g in enumerate(order, 1):
        member_rows = [rows[i] for i in groups[g]]
        name = _display_name([r.celltype_label for r in member_rows])
        genes = sorted({gene for r in member_rows for gene in r.matched_user_genes})
        quote = member_rows[0].evidence_snippet.replace("\n", " ")[:160]
        field = member_rows[0].field.label if member_rows[0].field else "not stated"
        lines.append(f'{line_no}. "{name}" | field: {field} | genes: {", ".join(genes)} | quote: "{quote}"')
        line_to_group[line_no] = g
        names[line_no] = {r.celltype_label for r in member_rows} | {name}

    out = llm.complete_json(
        system=LABEL_MERGE_SYSTEM,
        user=LABEL_MERGE_USER_TEMPLATE.format(label_lines="\n".join(lines)),
        schema=_MergeOutput,
    )

    systems = {g: _group_system([rows[i] for i in groups[g]]) for g in range(len(groups))}
    merges: list[tuple[str, list[int]]] = []
    used: set[int] = set()
    for grp in out.groups:
        members = []
        for m in grp.members:
            if isinstance(m, int) and m in line_to_group and m not in used and m not in members:
                members.append(m)
        if len(members) < 2:
            continue
        used.update(members)
        # The shared name is chosen afterwards from the papers' own names (the one
        # most papers use), never taken from the model.
        for part in _split_by_system([line_to_group[m] for m in members], systems):
            merges.append(("", part))
    return merges


def run_label_agent(rows: list[EvidenceRow], llm=None) -> tuple[list[LabelGroup], list[str]]:
    """Set ``label_group`` / ``normalized_label`` on each row. Returns (groups with
    several names, audit notes)."""
    notes: list[str] = []
    if not rows:
        return [], notes

    uf = _UnionFind(len(rows))
    first_by_key: dict[str, int] = {}
    for i, row in enumerate(rows):
        for text in (row.celltype_label, row.normalized_label):
            key = label_key(text)
            if not key:
                continue
            if key in first_by_key:
                uf.union(first_by_key[key], i)
            else:
                first_by_key[key] = i

    buckets: dict[int, list[int]] = defaultdict(list)
    for i in range(len(rows)):
        buckets[uf.find(i)].append(i)
    groups = list(buckets.values())
    names_before = len({r.celltype_label for r in rows})
    notes.append(f"{names_before} cell-type names reduced to {len(groups)} by format matching")

    chosen_name: dict[int, str] = {}
    if llm is not None and len(groups) > 1:
        try:
            merges = _llm_merges(groups, rows, llm)
            if merges:
                merged_away: set[int] = set()
                new_groups: list[list[int]] = []
                for name, gidx in merges:
                    combined = [i for g in gidx for i in groups[g]]
                    merged_away.update(gidx)
                    new_groups.append(combined)
                    if name:
                        chosen_name[len(new_groups) - 1] = name
                keep = [g for k, g in enumerate(groups) if k not in merged_away]
                groups = new_groups + keep
                notes.append(f"AI grouping merged synonyms into {len(groups)} cell populations")
        except Exception as exc:  # grouping must never break a run
            logger.warning("Label grouping LLM pass failed: %s", exc)
            notes.append("AI grouping skipped (format matching only)")

    result: list[LabelGroup] = []
    for k, members in enumerate(groups):
        labels = [rows[i].celltype_label for i in members]
        name = chosen_name.get(k) or _display_name(labels)
        for i in members:
            rows[i].label_group = name
            rows[i].normalized_label = name.lower()
        distinct = sorted(set(labels), key=lambda s: (-labels.count(s), s))
        if len(distinct) > 1:
            result.append(LabelGroup(name=name, members=distinct, row_count=len(members)))

    result.sort(key=lambda g: -g.row_count)
    return result, notes
