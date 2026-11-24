"""Local, per-user settings for MAPLES.

Settings (LLM key, endpoint, model, NCBI contact) live in ``~/.maples/config.json``
on the user's own machine, readable only by them. Nothing here is ever sent anywhere
except to the LLM endpoint and literature APIs the user configures.

Precedence, highest first:
  1. real environment variables (and a ``.env`` in the working directory)
  2. ``~/.maples/config.json``
  3. built-in defaults

Override the folder with ``MAPLES_HOME``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Keys a user may store. Everything else in the file is ignored.
SETTING_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "LLM_PROVIDER",
    "NCBI_EMAIL",
    "NCBI_API_KEY",
)
SECRET_KEYS: frozenset[str] = frozenset({"OPENAI_API_KEY", "NCBI_API_KEY"})

# Presets shown in the settings screen. All use the OpenAI-compatible API.
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {"label": "OpenAI", "base_url": "", "model": "gpt-4.1-mini"},
    "blablador": {
        "label": "Blablador (Helmholtz)",
        "base_url": "https://api.blablador.fz-juelich.de/v1",
        "model": "",
    },
    "ollama": {
        "label": "Local model (Ollama)",
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1",
    },
    "custom": {"label": "Other OpenAI-compatible", "base_url": "", "model": ""},
}


def maples_home() -> Path:
    """Folder holding config and cache (created on demand)."""
    home = Path(os.getenv("MAPLES_HOME") or Path.home() / ".maples").expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home


def config_path() -> Path:
    return maples_home() / "config.json"


def cache_dir() -> Path:
    """Disk cache for PubMed, full text and LLM results."""
    override = os.getenv("MAPLES_CACHE_DIR")
    path = Path(override).expanduser() if override else maples_home() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_settings() -> dict[str, str]:
    """Read stored settings (empty dict if none or unreadable)."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if k in SETTING_KEYS and v is not None}


def save_settings(values: dict[str, Any]) -> dict[str, str]:
    """Merge ``values`` into the stored settings and write them with 0600 permissions.

    An empty string removes a setting. Returns the merged settings.
    """
    current = load_settings()
    for key, value in values.items():
        if key not in SETTING_KEYS or value is None:
            continue
        value = str(value).strip()
        if value:
            current[key] = value
        else:
            current.pop(key, None)

    path = config_path()
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(current, fh, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return current


def apply_to_env(settings: dict[str, str] | None = None, *, override: bool = False) -> None:
    """Copy stored settings into ``os.environ`` so the pipeline picks them up.

    With ``override=False`` (startup), real environment variables win. With
    ``override=True`` (the user just saved new settings), stored values win and
    removed settings are cleared from the environment.
    """
    settings = load_settings() if settings is None else settings
    for key in SETTING_KEYS:
        if key in settings:
            if override or not os.environ.get(key):
                os.environ[key] = settings[key]
        elif override:
            os.environ.pop(key, None)


def bootstrap() -> None:
    """Load ``.env`` from the working directory, then stored settings. Call once at startup."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    apply_to_env(override=False)


def mask(value: str) -> str:
    """Show only enough of a secret to recognise it."""
    if not value:
        return ""
    return "•" * 6 + value[-4:] if len(value) > 8 else "•" * 6


def hosted_mode() -> bool:
    """True when MAPLES runs as a shared website (e.g. a Hugging Face Space).

    Then the AI key and service come only from the host's environment (Space
    secrets): visitors cannot see, replace or redirect them, so nobody can point
    the service address at their own server and receive the host's key.
    """
    flag = lambda k: os.getenv(k, "").strip().lower() in ("1", "true", "yes")
    return flag("MAPLES_HOSTED") or flag("USE_ENV_TOKEN_ONLY")


def public_view() -> dict[str, Any]:
    """Current effective settings, safe to show in the UI (secrets masked)."""
    if hosted_mode():
        preset = next((k for k, p in PROVIDER_PRESETS.items()
                       if p["base_url"] and p["base_url"] in os.environ.get("OPENAI_BASE_URL", "")), "custom")
        return {
            "hosted": True,
            "has_llm_key": bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("GITLAB_TOKEN")),
            "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", ""),
            "service_label": PROVIDER_PRESETS.get(preset, {}).get("label", "AI service set by the host"),
            "presets": {},
        }
    view: dict[str, Any] = {"hosted": False}
    for key in SETTING_KEYS:
        value = os.environ.get(key, "")
        view[key] = mask(value) if key in SECRET_KEYS else value
    view["has_llm_key"] = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("GITLAB_TOKEN"))
    view["config_path"] = str(config_path())
    view["presets"] = PROVIDER_PRESETS
    return view
