"""Sanitize text for Chainlit markdown rendering."""

from __future__ import annotations

import re

import pandas as pd


def safe_str(value: object, default: str = "") -> str:
    """Coerce values to strings; avoid nulls that break the Chainlit UI."""
    if value is None:
        return default
    if isinstance(value, float) and pd.isna(value):
        return default
    text = str(value).strip()
    return text if text else default


def sanitize_markdown_for_chainlit(text: str) -> str:
    """
    Strip patterns that crash Chainlit 2.11 markdown (null img src → startsWith error).
    """
    if not text:
        return ""

    # Chainlit img handler calls src.startsWith() without a null guard.
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[image]", text)
    text = re.sub(r"!\[[^\]]*\]", "[image]", text)
    text = re.sub(r"<img\b[^>]*>", "[image]", text, flags=re.IGNORECASE)
    # Empty-target links can also produce invalid href/src in the renderer.
    text = re.sub(r"\[([^\]]+)\]\(\s*\)", r"\1", text)
    # Remove any remaining markdown links with empty URLs
    text = re.sub(r"\[([^\]]+)\]\(None\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(\)", r"\1", text)
    # CRITICAL: Remove any links with null/whitespace-only URLs
    text = re.sub(r"\[([^\]]+)\]\(\s*null\s*\)", r"\1", text, flags=re.IGNORECASE)
    # Final safety: remove any markdown links that have empty parentheses or None
    text = re.sub(r"\[([^\]]+)\]\((?:null|undefined|None|\s*)\)", r"\1", text)
    return text


def markdown_cell(value: object, default: str = "") -> str:
    """
    Safe string for markdown tables and message bodies.

    Removes newlines that break table formatting.
    """
    text = sanitize_markdown_for_chainlit(safe_str(value, default))
    # Remove newlines that break markdown table cells
    text = text.replace("\n", " ").replace("\r", " ")
    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text)
    return text.strip()
