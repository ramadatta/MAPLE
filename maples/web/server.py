"""Local web app for MAPLES (``maples ui``).

Runs on the user's own machine and binds to 127.0.0.1 by default. The browser page
talks to this small API; the API runs the same pipeline as the Python API.

Endpoints
---------
GET  /                      the app page
GET  /api/settings          current settings (secrets masked)
POST /api/settings          save settings to ~/.maples/config.json
POST /api/settings/test     check the LLM connection with a tiny request
POST /api/run               start an analysis, returns a job id
GET  /api/run/{job_id}      progress, then the result
"""
from __future__ import annotations

import asyncio
import os
import logging
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from maples import settings as user_settings

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_JOBS = 20

# Pipeline stage labels (from the orchestrator) → plain-language steps for the page.
STAGES: list[tuple[str, str]] = [
    ("Retrieval Agent", "Searching the literature"),
    ("Evidence Extraction", "Reading papers and copying quotes"),
    ("Candidate Agent", "Grouping cell types"),
    ("Devil's Advocate", "Looking for weaknesses"),
    ("Consensus Agent", "Writing the summary"),
]
_STAGE_INDEX = {key: i for i, (key, _) in enumerate(STAGES)}

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
# On a shared website, how many analyses may run at once (each takes 3-6 minutes
# and uses the host's AI quota); more get a "busy, try again" message.
MAX_HOSTED_RUNS = int(os.getenv("MAPLES_MAX_HOSTED_RUNS", "3"))


class RunRequest(BaseModel):
    genes: str
    tissue: Optional[str] = None
    disease: Optional[str] = None
    species: Optional[str] = None


class Job:
    def __init__(self, genes: list[str], request: RunRequest, input_notes: Optional[list[str]] = None):
        self.id = uuid.uuid4().hex[:12]
        self.genes = genes
        self.input_notes = list(input_notes or [])
        self.request = request
        self.status = "running"  # running | done | error
        self.stage_index = 0
        self.detail = ""
        self.started = time.time()
        self.finished: Optional[float] = None
        self.result: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None
        # Live view while papers are read: counts plus the latest quotes.
        self.papers_done = 0
        self.papers_total = 0
        self.live_rows: list[dict[str, Any]] = []
        # Live status of slow sources, e.g. {"europepmc": {"searches_done": 9, ...}}
        self.sources: dict[str, dict[str, Any]] = {}

    def report_source(self, source: str, fields: dict[str, Any]) -> None:
        # Called from worker threads; replace the dict so readers never see it half-updated.
        self.sources = {**self.sources, source: {**self.sources.get(source, {}), **fields}}

    def to_dict(self) -> dict[str, Any]:
        end = self.finished or time.time()
        return {
            "id": self.id,
            "status": self.status,
            "genes": self.genes,
            "input_notes": self.input_notes,
            "stages": [label for _, label in STAGES],
            "stage_index": self.stage_index,
            "detail": self.detail,
            "elapsed_seconds": round(end - self.started, 1),
            "live": {
                "papers_done": self.papers_done,
                "papers_total": self.papers_total,
                "quotes": len(self.live_rows),
                "recent": self.live_rows[-6:][::-1],
            },
            "sources": self.sources,
            "result": self.result,
            "error": self.error,
        }


def _pool_payload(retrieval, extraction, evidence: list) -> dict[str, Any]:
    """Single-cell pool facts plus every pool paper and what happened to it."""
    facts = dict(getattr(retrieval, "pool", None) or {})
    if not facts or retrieval is None:
        return facts
    read = set(getattr(extraction, "pool_read", []) or [])
    screened = set(getattr(extraction, "pool_screened_out", []) or [])
    quoted = {e.get("pmid") for e in evidence}
    papers = []
    for p in retrieval.retrieved_papers:
        if not p.in_pool:
            continue
        if p.pmid in read:
            status = "quoted" if p.pmid in quoted else "read_no_quote"
        elif p.pmid in screened:
            status = "screened_out"
        else:
            status = "not_read"
        papers.append({"pmid": p.pmid, "title": p.title, "year": p.publication_year,
                       "journal": p.journal, "status": status})
    order = {"quoted": 0, "read_no_quote": 1, "not_read": 2, "screened_out": 3}
    papers.sort(key=lambda x: (order[x["status"]], -(x["year"] or 0)))
    counts = {k: sum(1 for x in papers if x["status"] == k) for k in order}
    return {**facts, **counts, "papers": papers}


def _result_payload(result) -> dict[str, Any]:
    """Public result plus the extra run facts the page shows."""
    payload = result.to_dict()
    state = result.raw_state
    retrieval = getattr(state, "retrieval", None)
    extraction = getattr(state, "extraction", None)
    devils = getattr(state, "devils_advocate", None)
    payload["coverage"] = {
        "papers_considered": len(retrieval.retrieved_papers) if retrieval else 0,
        "full_text_papers": retrieval.fulltext_count if retrieval else 0,
        "abstract_only_papers": extraction.abstract_only_papers if extraction else 0,
        "papers_excluded": extraction.excluded_paper_count if extraction else 0,
        "quotes_rejected": extraction.validation_rejected_rows if extraction else 0,
    }
    payload["sc_pool"] = _pool_payload(retrieval, extraction, payload.get("evidence") or [])
    payload["critique"] = (
        {
            "summary": devils.critique_summary,
            "strongest_counterargument": devils.strongest_counterargument,
            "nonspecific_genes": list(devils.nonspecific_genes),
        }
        if devils
        else None
    )
    payload["audit_trail"] = (
        [e.model_dump(mode="json") for e in state.audit_trail] if state is not None else []
    )
    return payload


def create_app() -> FastAPI:
    app = FastAPI(title="MAPLES", docs_url=None, redoc_url=None, openapi_url=None)
    jobs: "OrderedDict[str, Job]" = OrderedDict()

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Defend the local server (which holds the user's key) against other web
        # pages: refuse foreign Host headers (DNS rebinding) and cross-site writes.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        allowed_hosts = _LOCAL_HOSTS | set(getattr(request.app.state, "extra_hosts", ()))
        if "*" in allowed_hosts:  # explicitly opened up with `maples ui --host ...`
            return await call_next(request)
        if host not in allowed_hosts:
            return JSONResponse({"detail": "Unknown host"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).hostname not in {h.strip("[]") for h in allowed_hosts}:
                return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/leaf.svg")
    def leaf():
        return FileResponse(STATIC_DIR / "leaf.svg", media_type="image/svg+xml",
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/settings")
    async def get_settings():
        return user_settings.public_view()

    @app.post("/api/settings")
    async def post_settings(values: dict[str, Any]):
        if user_settings.hosted_mode():
            raise HTTPException(403, "Settings are fixed on this hosted version of MAPLES.")
        clean: dict[str, Any] = {}
        for key, value in values.items():
            if key not in user_settings.SETTING_KEYS:
                continue
            # The page shows masked secrets; an untouched masked value means "keep".
            if key in user_settings.SECRET_KEYS and isinstance(value, str) and value.startswith("•"):
                continue
            clean[key] = value
        saved = user_settings.save_settings(clean)
        user_settings.apply_to_env(saved, override=True)
        return user_settings.public_view()

    @app.post("/api/settings/test")
    async def test_settings():
        if user_settings.hosted_mode():
            raise HTTPException(403, "Settings are fixed on this hosted version of MAPLES.")
        from maples.api import _make_llm_from_env

        llm = _make_llm_from_env()
        if llm is None:
            raise HTTPException(400, "No API key is set. Add one in Settings.")
        try:
            client = getattr(llm, "client", None)
            if client is None:
                return {"ok": True, "message": "Key saved. This provider is checked on the first run."}
            await asyncio.to_thread(
                client.chat.completions.create,
                model=llm.model,
                messages=[{"role": "user", "content": "Reply with the word OK."}],
                max_tokens=5,
            )
            return {"ok": True, "message": f"Connected to {llm.model}."}
        except Exception as exc:  # show the provider's own error text
            return {"ok": False, "message": str(exc)[:400]}

    @app.post("/api/run")
    async def start_run(req: RunRequest):
        from maples.api import _make_llm_from_env
        from maples.utils.gene_parser import parse_genes

        if user_settings.hosted_mode():
            running = sum(1 for j in jobs.values() if j.status == "running")
            if running >= MAX_HOSTED_RUNS:
                raise HTTPException(
                    429,
                    f"MAPLES is busy with {running} other analyses right now. Please try again in a few "
                    "minutes, or install MAPLES on your own computer to run it any time.",
                )
        genes, input_notes = parse_genes(req.genes or "")
        if not genes:
            raise HTTPException(400, "No gene symbols found. Paste genes separated by commas, spaces or new lines.")
        llm = _make_llm_from_env()
        if llm is None:
            raise HTTPException(400, "No API key is set. Open Settings and add your key first.")

        job = Job(genes, req, input_notes)
        jobs[job.id] = job
        while len(jobs) > MAX_JOBS:
            jobs.popitem(last=False)

        async def progress(label: str, detail: str) -> None:
            job.stage_index = _STAGE_INDEX.get(label, job.stage_index)
            job.detail = detail

        def partial(done: int, total: int, rows: list) -> None:
            # Called from a worker thread; only simple attribute updates here.
            job.papers_done, job.papers_total = done, total
            job.live_rows.extend(
                {
                    "celltype_label": r.celltype_label,
                    "genes": list(r.matched_user_genes),
                    "pmid": r.pmid,
                    "year": r.publication_year,
                    "snippet": r.evidence_snippet[:200],
                }
                for r in rows
            )

        async def run() -> None:
            from maples.api import annotate_async
            from maples.runtime import activity

            activity.set_reporter(job.report_source)  # this task's context only
            try:
                result = await annotate_async(
                    genes,
                    tissue=req.tissue,
                    disease=req.disease,
                    species=req.species,
                    llm=llm,
                    include_raw_state=True,
                    progress_callback=progress,
                    partial_callback=partial,
                )
                job.result = _result_payload(result)
                job.status = "done"
                job.stage_index = len(STAGES)
            except Exception as exc:
                logger.exception("Run %s failed", job.id)
                job.status = "error"
                job.error = str(exc)
            finally:
                job.finished = time.time()

        job.task = asyncio.create_task(run())  # keep a reference so it is not GC'd
        return job.to_dict()

    @app.get("/api/run/{job_id}")
    async def get_run(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown run. It may have expired; start it again.")
        return job.to_dict()

    return app


app = create_app()
