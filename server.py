"""Thermal Layer API. Run: .venv/bin/uvicorn server:app --reload --port 8000"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import agent

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "outputs" / "runs"
REPORT_CACHE_PATH = ROOT / "outputs" / "report_cache.json"
WEB_DIR = ROOT / "web"
ENV_VARS = ["ANTHROPIC_API_KEY", "KEEWANO_API_KEY", "KEEWANO_PAT", "KEEWANO_MCP_URL",
            "KEEWANO_EMAIL", "KEEWANO_PROJECT_ID"]
SCENARIOS = {"normal", "heat_wave", "cold_snap", "server_outage", "agent"}

app = FastAPI(title="Thermal Layer")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_lock = threading.Lock()
_report_cache: dict[str, dict] = {}
JOBS: dict[str, dict] = {}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _load_report_cache() -> dict:
    try:
        return json.loads(REPORT_CACHE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_report_cache.update(_load_report_cache())


class AskBody(BaseModel):
    question: str
    run_id: str | None = None


@app.post("/ask")
def post_ask(body: AskBody):
    # sync def -> runs in a worker thread, so agent.ask can start its own event loop
    try:
        return agent.ask(body.question, run_id=body.run_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"ask failed: {e}") from e


@app.get("/report")
def get_report(run_id: str | None = None):
    run_id = run_id or agent.latest_run("normal")
    if not run_id:
        raise HTTPException(status_code=404, detail="no normal run in outputs/runs/index.json")
    try:
        import keewano
        report = keewano.run_report(run_id)
        entry = {"run_id": run_id, "report": report, "timestamp": _now()}
        with _lock:
            _report_cache[run_id] = entry
            REPORT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPORT_CACHE_PATH.write_text(json.dumps(_report_cache, indent=1, default=str))
        return {**entry, "cached": False}
    except Exception as e:  # noqa: BLE001
        cached = _report_cache.get(run_id)
        if cached:
            return {**cached, "cached": True, "error": str(e)[:300]}
        raise HTTPException(status_code=503, detail=f"report failed and nothing cached: {e}") from e


@app.get("/decisions")
def get_decisions(run_id: str | None = None):
    if run_id:
        path = RUNS_DIR / f"decisions_{run_id}.json"
    else:
        files = sorted(RUNS_DIR.glob("decisions_*.json"), key=lambda p: p.stat().st_mtime)
        if not files:
            raise HTTPException(status_code=404, detail="no decisions logged yet")
        path = files[-1]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path.name} not found")
    return json.loads(path.read_text())


@app.get("/runs")
def get_runs():
    return agent.load_index()


def _run_job(run_id: str, scenario: str) -> None:
    JOBS[run_id].update(status="simulating")
    try:
        import thermal
        run = thermal.simulate(scenario=scenario, run_id=run_id)
        JOBS[run_id].update(status="saving", path=str(thermal.save_run(run)))
        import keewano
        JOBS[run_id].update(status="pushing")
        JOBS[run_id].update(status="done", push=keewano.send_run(run), finished=_now())
    except Exception as e:  # noqa: BLE001
        JOBS[run_id].update(status="error", error=str(e)[:500], finished=_now())


@app.post("/run")
def post_run(background: BackgroundTasks, scenario: str = "normal"):
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"scenario must be one of {sorted(SCENARIOS)}")
    run_id = f"{scenario}-{uuid.uuid4().hex[:4]}"
    JOBS[run_id] = {"run_id": run_id, "scenario": scenario, "status": "queued", "started": _now()}
    background.add_task(_run_job, run_id, scenario)
    return {"run_id": run_id, "status": "queued"}


@app.get("/jobs")
def get_jobs():
    return list(JOBS.values())


@app.get("/health")
def health():
    return {"ok": True, "env": {k: bool(os.getenv(k)) for k in ENV_VARS}}


if (WEB_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    @app.get("/")
    def root():
        return {"service": "thermal-layer", "endpoints": ["/ask", "/report", "/decisions", "/runs",
                                                          "/run", "/jobs", "/health"]}
