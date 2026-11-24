"""Tests for local settings, the CLI and the local web app (no network)."""
import os
import stat

import pytest
from fastapi.testclient import TestClient

from maples import settings as user_settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPLES_HOME", str(tmp_path))
    for key in user_settings.SETTING_KEYS + ("GITLAB_TOKEN",):
        # setenv first so monkeypatch restores the original state afterwards, even
        # for variables the code under test sets itself (apply_to_env).
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    return tmp_path


def test_save_and_load_settings_private_file(home):
    user_settings.save_settings({"OPENAI_API_KEY": "sk-test-123456789", "OPENAI_MODEL": "m1", "BOGUS": "x"})
    path = home / "config.json"
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = user_settings.load_settings()
    assert loaded == {"OPENAI_API_KEY": "sk-test-123456789", "OPENAI_MODEL": "m1"}


def test_empty_value_removes_setting(home):
    user_settings.save_settings({"OPENAI_MODEL": "m1"})
    user_settings.save_settings({"OPENAI_MODEL": ""})
    assert "OPENAI_MODEL" not in user_settings.load_settings()


def test_env_wins_at_startup_but_saved_wins_after_save(home, monkeypatch):
    user_settings.save_settings({"OPENAI_MODEL": "from-file"})
    monkeypatch.setenv("OPENAI_MODEL", "from-env")
    user_settings.apply_to_env(override=False)
    assert os.environ["OPENAI_MODEL"] == "from-env"
    user_settings.apply_to_env(override=True)
    assert os.environ["OPENAI_MODEL"] == "from-file"


def test_public_view_masks_secrets(home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-abcdefghijklmnop")
    view = user_settings.public_view()
    assert "abcdefghijkl" not in view["OPENAI_API_KEY"]
    assert view["OPENAI_API_KEY"].endswith("mnop")
    assert view["has_llm_key"] is True


def test_cache_lives_under_maples_home(home):
    from maples.services.cache_service import CacheService

    cache = CacheService()
    assert str(cache.cache_dir).startswith(str(home))


@pytest.fixture
def client(home):
    from maples.web.server import create_app

    return TestClient(create_app(), base_url="http://127.0.0.1:8765")


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "MAPLES" in r.text


def test_settings_roundtrip_never_returns_key(client):
    r = client.post("/api/settings", json={"OPENAI_API_KEY": "sk-secret-value-9999", "OPENAI_MODEL": "m2"})
    assert r.status_code == 200
    assert "sk-secret-value" not in r.text
    assert r.json()["has_llm_key"] is True
    # Posting the masked value back keeps the stored key.
    masked = r.json()["OPENAI_API_KEY"]
    client.post("/api/settings", json={"OPENAI_API_KEY": masked})
    assert user_settings.load_settings()["OPENAI_API_KEY"] == "sk-secret-value-9999"


def test_foreign_host_refused(home):
    from maples.web.server import create_app

    c = TestClient(create_app(), base_url="http://evil.example.com")
    assert c.get("/api/settings").status_code == 403


def test_cross_site_post_refused(client):
    r = client.post("/api/settings", json={"OPENAI_MODEL": "x"}, headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_run_requires_key_and_genes(client):
    r = client.post("/api/run", json={"genes": "TP63 KRT17"})
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]
    client.post("/api/settings", json={"OPENAI_API_KEY": "sk-x-123456789"})
    r = client.post("/api/run", json={"genes": "   "})
    assert r.status_code == 400
    assert "gene" in r.json()["detail"].lower()


def test_run_reports_progress_and_result(client, monkeypatch):
    import maples.api as api_mod
    from maples.api import AnnotationResult

    client.post("/api/settings", json={"OPENAI_API_KEY": "sk-x-123456789"})

    async def fake_annotate(genes, **kwargs):
        await kwargs["progress_callback"]("Evidence Extraction", "reading")
        return AnnotationResult(label="fibroblast", confidence="High")

    monkeypatch.setattr(api_mod, "annotate_async", fake_annotate)
    job = client.post("/api/run", json={"genes": "COL1A1, DCN"}).json()
    assert job["genes"] == ["COL1A1", "DCN"]
    for _ in range(50):
        state = client.get(f"/api/run/{job['id']}").json()
        if state["status"] != "running":
            break
    assert state["status"] == "done", state
    assert state["result"]["label"] == "fibroblast"
    assert state["result"]["coverage"]["papers_considered"] == 0


def test_unknown_run_404(client):
    assert client.get("/api/run/nope").status_code == 404


def test_cli_config_set_and_show(home, capsys, monkeypatch):
    from maples.cli import main

    monkeypatch.setattr(user_settings, "bootstrap", lambda: None)  # never read the developer's .env

    assert main(["config", "--set", "OPENAI_MODEL=m3", "NCBI_EMAIL=a@b.org"]) == 0
    out = capsys.readouterr().out
    assert "m3" in out and "a@b.org" in out
    assert main(["config", "--set", "NOPE=1"]) == 2


def test_cli_version(capsys, monkeypatch):
    from maples.cli import main

    monkeypatch.setattr(user_settings, "bootstrap", lambda: None)

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip()


# --- hosted mode (Hugging Face Space) ---------------------------------------

@pytest.fixture
def hosted(home, monkeypatch):
    from maples.web.server import create_app

    monkeypatch.setenv("MAPLES_HOSTED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host-secret-key-123456")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.blablador.fz-juelich.de/v1")
    monkeypatch.setenv("OPENAI_MODEL", "alias-fast")
    return TestClient(create_app(), base_url="http://127.0.0.1:8765")


def test_hosted_settings_show_no_key_or_paths(hosted):
    view = hosted.get("/api/settings").json()
    assert view["hosted"] is True and view["has_llm_key"] is True
    assert view["service_label"] == "Blablador (Helmholtz)"
    text = str(view)
    assert "sk-host" not in text and "OPENAI_API_KEY" not in text and "config_path" not in text


def test_hosted_settings_cannot_be_changed_or_redirected(hosted):
    r = hosted.post("/api/settings", json={"OPENAI_BASE_URL": "https://attacker.example/v1"})
    assert r.status_code == 403
    assert hosted.post("/api/settings/test").status_code == 403
    import os
    assert os.environ["OPENAI_BASE_URL"] == "https://api.blablador.fz-juelich.de/v1"


def test_hosted_limits_parallel_runs(hosted, monkeypatch):
    import maples.web.server as srv

    monkeypatch.setattr(srv, "MAX_HOSTED_RUNS", 0)
    r = hosted.post("/api/run", json={"genes": "TP63 KRT17"})
    assert r.status_code == 429 and "busy" in r.json()["detail"]


def test_leaf_icon_served_as_svg(client):
    r = client.get("/leaf.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
