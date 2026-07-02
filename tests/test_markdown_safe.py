"""Tests for Chainlit markdown sanitization."""

from utils.markdown_safe import markdown_cell, sanitize_markdown_for_chainlit


def test_sanitize_strips_markdown_images():
    raw = "See ![Figure 1](http://example.com/x.png) in paper"
    assert "![Figure" not in sanitize_markdown_for_chainlit(raw)
    assert "[image]" in sanitize_markdown_for_chainlit(raw)


def test_sanitize_strips_html_images():
    raw = 'Title with <img src="http://x.com/a.png"> inline'
    assert "<img" not in sanitize_markdown_for_chainlit(raw)
    assert "[image]" in sanitize_markdown_for_chainlit(raw)


def test_sanitize_empty_markdown_links():
    assert sanitize_markdown_for_chainlit("[broken link]()") == "broken link"


def test_markdown_cell_handles_none():
    assert markdown_cell(None, "N/A") == "N/A"
