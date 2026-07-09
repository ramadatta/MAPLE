"""
MAPLE — Marker-based Annotation with PubMed Literature Evidence.
Chainlit application entry point (v2 — maple pipeline).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import chainlit as cl
from dotenv import load_dotenv

# ── Project root on sys.path ─────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Logging ──────────────────────────────────────────────────────────────────
# Level is configurable via MAPLE_LOG_LEVEL (default INFO). DEBUG everywhere is
# noisy and slows the pipeline in production; set MAPLE_LOG_LEVEL=DEBUG to trace.
_LOG_LEVEL = getattr(logging, os.getenv("MAPLE_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(_LOG_LEVEL)
for _ns in ("maple", "agents", "services", "utils", "models"):
    logging.getLogger(_ns).setLevel(_LOG_LEVEL)

load_dotenv(_PROJECT_ROOT / ".env")

# ── Core imports ─────────────────────────────────────────────────────────────
from maple.models import AnalysisInput, AnalysisState
from maple.runtime.orchestrator import run_maple_pipeline
from maple.input_parser import parse_user_message
from maple.ui.audit_trail import render_audit_trail_html
from maple.ui.components import render_consensus_panel_html, render_devils_advocate_html
from maple.ui.table import render_evidence_table_html, to_csv
from services.llm_service import (
    create_llm_service,
    detect_llm_provider,
    is_blablador_config,
    llm_provider_label,
)
from utils.ui_components import welcome_hero
from utils.progress_tracker import PipelineProgress
from utils.markdown_safe import markdown_cell

try:
    from services.gitlab_llm_service import GitLabLLMService
    _HAS_GITLAB = True
except ImportError:
    _HAS_GITLAB = False


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def send_html(content: str) -> None:
    """Send a styled HTML block (requires unsafe_allow_html in Chainlit config)."""
    wrapped = f'<div class="maple-wide-block">{content}</div>'
    await cl.Message(content=wrapped).send()


def _env_token_only() -> bool:
    """When true, use only API keys from .env (ignore chat paste)."""
    return os.getenv("USE_ENV_TOKEN_ONLY", "").lower() in ("1", "true", "yes")


def _get_env_api_key() -> str | None:
    """Read LLM API key from environment variables."""
    return os.getenv("OPENAI_API_KEY") or os.getenv("GITLAB_TOKEN") or None


def _get_api_key() -> str | None:
    """Get LLM API key from env and/or session."""
    if _env_token_only():
        return _get_env_api_key()
    env_key = _get_env_api_key()
    if env_key:
        return env_key
    return cl.user_session.get("api_key")


def _provider_label(api_key: str) -> str:
    """Human-readable provider name for the given token."""
    return llm_provider_label(api_key)


def _create_llm(api_key: str):
    """Create LLM client; honor LLM_PROVIDER when USE_ENV_TOKEN_ONLY is set."""
    provider = None
    if _env_token_only():
        env_provider = os.getenv("LLM_PROVIDER", "auto").lower()
        if env_provider != "auto":
            provider = env_provider
    return create_llm_service(api_key, provider=provider)


async def _ask_api_key() -> str | None:
    """Return a stored API key or prompt the user once."""
    if _env_token_only():
        token = _get_env_api_key()
        if token:
            return token
        await cl.Message(
            content=(
                "Set `OPENAI_API_KEY` (Blablador/OpenAI) or `GITLAB_TOKEN` (GitLab Duo) "
                "in `.env` — `USE_ENV_TOKEN_ONLY=true` requires one of them."
            )
        ).send()
        return None

    existing = _get_api_key()
    if existing:
        return existing

    res = await cl.AskUserMessage(
        content=(
            "Please paste your **API token** to continue:\n\n"
            "- **Blablador / Helmholtz Codebase PAT** (`glpat-...`)\n"
            "- **GitLab Duo PAT** (`glpat-...` with `api` + `ai_features`)\n"
            "- **OpenAI API key** (`sk-...`)\n\n"
            "Your token is used only for this local session and is not stored."
        ),
        timeout=300,
    ).send()

    if not res:
        return None
    key = res["output"].strip()
    if key:
        cl.user_session.set("api_key", key)
        await cl.Message(
            content=f"{_provider_label(key)} token received — it will be used only for this session."
        ).send()
    return key or None


def _context_note(analysis_input: AnalysisInput) -> str:
    """Short human-readable summary of optional search context."""
    parts: list[str] = []
    if analysis_input.tissue:
        parts.append(f"tissue: {analysis_input.tissue}")
    if analysis_input.disease:
        parts.append(f"disease: {analysis_input.disease}")
    if analysis_input.species:
        parts.append(f"species: {analysis_input.species}")
    if analysis_input.technology:
        parts.append(f"technology: {analysis_input.technology}")
    return ", ".join(parts)


# ─── Pipeline runner ──────────────────────────────────────────────────────────

async def _run_pipeline(analysis_input: AnalysisInput) -> None:
    """Obtain API key, run MAPLE pipeline, store state and render results."""

    # 1 — API key
    api_key = await _ask_api_key()
    if not api_key:
        await cl.Message(
            content=(
                "An API token is required. "
                "Set `OPENAI_API_KEY` in `.env` or paste your token."
            )
        ).send()
        return

    # 2 — LLM client
    llm = None
    if os.getenv("DISABLE_LLM", "").lower() not in ("1", "true", "yes"):
        if detect_llm_provider(api_key) == "gitlab" and _HAS_GITLAB:
            verify = os.getenv("VERIFY_GITLAB_TOKEN", "true").lower() not in ("0", "false", "no")
            if verify:
                ok, msg = GitLabLLMService.verify_pat(api_key)
                if ok:
                    await cl.Message(content=f"GitLab token verified: {markdown_cell(msg)}").send()
                else:
                    await cl.Message(
                        content=(
                            f"⚠️ GitLab auth issue: {markdown_cell(msg)}\n\n"
                            "Continuing with PubMed + marker rules only (LLM narratives may be limited)."
                        )
                    ).send()
                    ok = False  # skip LLM creation
            else:
                ok = True

            if ok:
                llm = _create_llm(api_key)
        else:
            llm = _create_llm(api_key)

    # 3 — Progress tracking
    gene_preview = ", ".join(analysis_input.markers[:8])
    if len(analysis_input.markers) > 8:
        gene_preview += f" … ({len(analysis_input.markers)} total)"

    status_lines = [
        f"Searching PubMed for **{len(analysis_input.markers)}** marker genes: `{gene_preview}`",
        "Discovering where these markers are annotated to cell types across published studies.",
    ]
    context_note = _context_note(analysis_input)
    if context_note:
        status_lines.append(
            f"Focusing the search on your context ({context_note})."
        )
    status_lines.append("This may take 1–3 minutes…")
    await cl.Message(content="\n\n".join(status_lines)).send()

    progress = PipelineProgress()
    await progress.start(
        f"Analyzing {len(analysis_input.markers)} marker genes",
        gene_preview,
    )

    async def step_callback(label: str, detail: str = "") -> None:
        await progress.update(label, detail)

    # 4 — Run pipeline
    try:
        state: AnalysisState = await run_maple_pipeline(
            analysis_input,
            llm=llm,
            progress_callback=step_callback,
        )
    except Exception as exc:
        logger.error("MAPLE pipeline error: %s", exc, exc_info=True)
        await progress.finish()
        await cl.Message(
            content=f"Pipeline error: {exc}. Please check your API key and try again."
        ).send()
        return

    # 5 — Store result
    cl.user_session.set("analysis_state", state)

    # 6 — Surface any pipeline errors
    for err in state.errors:
        await cl.Message(content=f"⚠️ {markdown_cell(err)}").send()

    # 7 — Plain-text summary (always visible even if HTML panels fail to render)
    if state.consensus:
        c = state.consensus
        paper_count = len(state.retrieval.retrieved_papers) if state.retrieval else 0
        evidence_count = len(state.extraction.evidence_rows) if state.extraction else 0
        await cl.Message(
            content=(
                f"**{markdown_cell(c.consensus_label)}** "
                f"({markdown_cell(c.confidence)} confidence)\n\n"
                f"{markdown_cell(c.consensus_rationale)}\n\n"
                f"Based on {paper_count} papers, {evidence_count} evidence rows. "
                f"**Use the evidence table below** to review each marker–cell-type association "
                f"and its tissue/disease context from the cited papers."
            )
        ).send()
    elif not state.errors:
        await cl.Message(
            content="Analysis finished but no consensus label could be produced."
        ).send()

    # 8 — Render result up to the evidence table (the primary deliverable).
    #     Detailed consensus / devil's-advocate panels are intentionally omitted.
    await send_html(render_audit_trail_html(state))
    await send_html(render_evidence_table_html(state))

    await progress.finish()


# ─── Table navigation ─────────────────────────────────────────────────────────

async def _handle_table_command(raw: str, state: AnalysisState) -> bool:
    """
    Handle table commands that still require server access (CSV export, reset).
    Sorting, filtering, and pagination are handled in the browser.
    """
    text = raw.strip().lower()

    # download csv
    if text in ("download csv", "csv", "export csv", "export"):
        csv_str   = to_csv(state)
        csv_bytes = csv_str.encode("utf-8")
        await cl.Message(
            content="Here is the full evidence table as CSV:",
            elements=[
                cl.File(
                    name    = "maple_evidence.csv",
                    content = csv_bytes,
                    mime    = "text/csv",
                )
            ],
        ).send()
        return True

    # reset / new search
    if text in ("reset", "new search", "restart", "start over", "new"):
        cl.user_session.set("analysis_state", None)
        await send_html(welcome_hero())
        return True

    return False  # not a recognised table command


# ─── Chainlit handlers ────────────────────────────────────────────────────────

@cl.set_starters
async def set_starters() -> list[cl.Starter]:
    """Example marker panels shown as one-click use cases."""
    return [
        cl.Starter(
            label="Aberrant basaloid (lung fibrosis)",
            message="TP63, CDH2, CDKN1A, CDKN2A, KRT17, VIM",
        ),
        cl.Starter(
            label="Fibroblast / ECM panel",
            message="COL1A1, COL3A1, POSTN, DCN",
        ),
    ]


@cl.on_chat_start
async def on_chat_start() -> None:
    """Initialise session and show welcome message."""
    cl.user_session.set("api_key", None)
    cl.user_session.set("analysis_state", None)
    await send_html(welcome_hero())


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Handle all user messages."""
    raw  = (message.content or "").strip()
    text = raw.lower()

    # ── 1. Table navigation (only when a result is loaded) ───────────────────
    state: AnalysisState | None = cl.user_session.get("analysis_state")
    if state is not None:
        handled = await _handle_table_command(raw, state)
        if handled:
            return

    # ── 2. API key paste (sk-... or glpat-...) ───────────────────────────────
    if not _env_token_only():
        is_openai = raw.startswith("sk-") or raw.startswith("sk_")
        is_gitlab = (
            _HAS_GITLAB and GitLabLLMService.is_gitlab_token(raw)
        )
        if is_openai or is_gitlab:
            cl.user_session.set("api_key", raw)
            await cl.Message(
                content=(
                    f"{_provider_label(raw)} token saved. "
                    "Paste your marker genes to start analysis."
                )
            ).send()
            return

    # ── 3. Detect marker genes in message ────────────────────────────────────
    analysis_input: AnalysisInput | None = parse_user_message(raw)
    if analysis_input and analysis_input.markers:
        await _run_pipeline(analysis_input)
        return

    # ── 4. Nothing recognised — nudge the user ───────────────────────────────
    if state is not None:
        # A result is already shown; tell them what commands are available
        await cl.Message(
            content=(
                "Results are shown above. "
                "Use the evidence table filter box and column headers to explore rows. "
                "Chat commands: `download csv` or `reset` for a new search."
            )
        ).send()
    else:
        await cl.Message(
            content=(
                "Please provide **marker genes** to search the literature.\n\n"
                "**Paste them in any format** — comma, tab, or one per line, or a "
                "Python/R list:\n"
                "- `TP63, CDH2, CDKN1A, KRT17, VIM`\n"
                "- `[\"COL1A1\", \"COL3A1\", \"POSTN\", \"DCN\"]`\n\n"
                "**Optional context** to focus the search — just say it naturally:\n"
                "- `COL1A1, COL3A1, POSTN, DCN — focus on lung, IPF`\n\n"
                "Context is optional; without it MAPLE searches all of PubMed.\n\n"
                "⚠️ Research use only — not a clinical or diagnostic tool."
            )
        ).send()
