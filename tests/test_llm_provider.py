"""Tests for LLM provider detection."""

import os

from services.llm_service import detect_llm_provider, is_blablador_config, llm_provider_label


def test_detect_gitlab_token():
    assert detect_llm_provider("glpat-abc123") == "gitlab"
    assert detect_llm_provider("glptt-abc123") == "gitlab"


def test_detect_openai_token():
    assert detect_llm_provider("sk-abc123") == "openai"


def test_blablador_glpat_routes_to_openai(monkeypatch):
    monkeypatch.setenv(
        "OPENAI_BASE_URL", "https://api.blablador.fz-juelich.de/v1"
    )
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    assert detect_llm_provider("glpat-abc123") == "openai"
    assert is_blablador_config() is True
    assert llm_provider_label("glpat-abc123") == "Blablador"


def test_explicit_provider_override():
    assert detect_llm_provider("sk-abc", provider="gitlab") == "gitlab"
