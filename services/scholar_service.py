"""Google Scholar discovery via the Smithery `google/scholar` MCP server.

Google Scholar indexes full-text bodies, so it can surface papers whose marker
genes appear only in the body (and are therefore missed by PubMed/Europe PMC
abstract search). MAPLE uses it as a *finder* only: results are resolved to a
DOI/PMID and the actual evidence text is read from an open copy (PMC/bioRxiv).

Requires the `mcp` package and a Smithery endpoint (optionally an API key).
Fails soft: any error yields an empty result set so the pipeline keeps running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_TOOL_NAME = "search_papers"


def _extract_year(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(m.group(0)) if m else None


def _first_pdf(resources: Any) -> str:
    if isinstance(resources, list):
        for r in resources:
            if isinstance(r, dict):
                fmt = str(r.get("file_format") or r.get("fileFormat") or "").upper()
                link = r.get("link") or r.get("url") or ""
                if link and ("PDF" in fmt or link.lower().endswith(".pdf")):
                    return link
    return ""


def _coerce_json(raw: Any) -> Any:
    """Parse JSON, tolerating prose wrappers / code fences around it."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    # Fall back to the first JSON object/array embedded in the text.
    m = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (ValueError, TypeError):
            return None
    return None


def _find_result_list(obj: Any, depth: int = 0) -> list | None:
    """Recursively locate the first list of paper-like dicts in any nesting."""
    if isinstance(obj, list):
        if any(isinstance(x, dict) and ("title" in x or "name" in x) for x in obj):
            return obj
        for x in obj:
            found = _find_result_list(x, depth + 1)
            if found:
                return found
    elif isinstance(obj, dict) and depth < 6:
        # Prefer well-known result keys first, then search all values.
        for key in ("organic_results", "results", "papers", "articles", "data", "items"):
            if isinstance(obj.get(key), list):
                found = _find_result_list(obj[key], depth + 1)
                if found:
                    return found
        for v in obj.values():
            found = _find_result_list(v, depth + 1)
            if found:
                return found
    return None


def normalize_scholar_results(raw: Any) -> list[dict]:
    """Flatten a SerpAPI-style Scholar payload into simple hit dicts (shape-agnostic)."""
    raw = _coerce_json(raw)
    if raw is None:
        return []
    results = _find_result_list(raw)
    if results is None:
        results = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []

    hits: list[dict] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        pub_info = item.get("publication_info") or {}
        summary = pub_info.get("summary") if isinstance(pub_info, dict) else ""
        inline = item.get("inline_links") or {}
        cited = 0
        if isinstance(inline, dict):
            cb = inline.get("cited_by") or {}
            if isinstance(cb, dict):
                cited = cb.get("total") or 0
        hits.append(
            {
                "title": title,
                "link": item.get("link") or item.get("url") or "",
                "pdf_url": _first_pdf(item.get("resources")) or item.get("pdf_url") or "",
                "snippet": item.get("snippet") or "",
                "year": item.get("year") or _extract_year(summary or ""),
                "citations": cited,
            }
        )
    return hits


class ScholarService:
    """Thin MCP client for the Smithery Google Scholar server."""

    def __init__(self, url: str, api_key: str = "", timeout: int = 30):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def _endpoint(self) -> tuple[str, dict]:
        url = self.url
        headers: dict[str, str] = {}
        if self.api_key:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={self.api_key}"
            headers["Authorization"] = f"Bearer {self.api_key}"
        return url, headers

    async def _acall(self, query: str, num: int) -> Any:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        url, headers = self._endpoint()
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    _TOOL_NAME, {"q": query, "num": max(1, min(num, 20))}
                )
                if getattr(result, "structuredContent", None):
                    return result.structuredContent
                for block in getattr(result, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        return text
                return None

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Run a Scholar search; returns [] on any failure."""
        try:
            raw = asyncio.run(asyncio.wait_for(self._acall(query, max_results), self.timeout))
        except Exception as exc:  # noqa: BLE001 - discovery must never break the pipeline
            logger.warning("Scholar (Smithery) search failed: %s", exc)
            return []
        hits = normalize_scholar_results(raw)[:max_results]
        if not hits:
            # Show the raw payload shape so an unexpected response can be diagnosed.
            logger.warning("Scholar returned 0 parseable hits; raw payload: %s", repr(raw)[:1200])
        return hits
