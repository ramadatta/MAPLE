"""Pytest configuration — add project root to sys.path."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep tests away from the real ~/.maples (settings and cache) of whoever runs them.
import os
import tempfile

os.environ.setdefault("MAPLES_HOME", tempfile.mkdtemp(prefix="maples-test-home-"))

import pytest


@pytest.fixture(autouse=True)
def _no_single_cell_pool_network(monkeypatch):
    """The single-cell pool searches PubMed for real; tests that want it turn it back on."""
    from maples import config as cfg

    monkeypatch.setattr(cfg, "ENABLE_SC_POOL", False)
