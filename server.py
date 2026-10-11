"""Alpine API. /run, /ask, /report, /decisions.

Until keewano.py and agent.py are wired, /report and /decisions serve the last run's
numbers from out/ so the web page works end to end. Set ALPINE_STUB=0 to require Keewano.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()
OUT = Path("out")
OUT.mkdir(exist_ok=True)

app = FastAPI(title="Alpine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Ask(BaseModel):
    question: str


def _latest_run() -> dict | None:
    runs = sorted(OUT.glob("run_*.json"))
    return json.loads(runs[-1].read_text()) if runs else None


@app.post("/run")
def run(scenario: str = "normal", push: bool = True):
    import thermal
    result = thermal.run(scenario=scenario, push=push)
    return {"run_id": result["run_id"], "events": result["n_events"], "summary": result["summary"]}


@app.get("/report")
def report():
    """Three metrics, each the result of one Keewano query. Falls back to the last local run."""
    try:
        import agent
        return agent.report()
    except Exception as e:  # noqa: BLE001 - stub path until Keewano is wired
        run = _latest_run()
        if run is None:
            raise HTTPException(503, f"no run yet and Keewano not reachable: {e}")
        s = run["summary"]
        return {
            "run_id": run["run_id"],
            "cached": True,
            "computed_at": run["computed_at"],
            "metrics": [
                {"name": "PUE", "value": s["pue"], "unit": "facility kWh / IT kWh",
                 "query": "SELECT sum(power_kwh) / sum(it_kwh) FROM events WHERE run_id = :run"},
                {"name": "WUE", "value": s["wue_l_per_kwh"], "unit": "L / IT kWh",
                 "query": "SELECT sum(water_gal) * 3.785 / sum(it_kwh) FROM events WHERE run_id = :run"},
                {"name": "ERF", "value": s["erf"], "unit": "heat reused / energy in",
                 "query": "SELECT sum(heat_exported_mw) / (sum(power_kwh) / 1000) FROM events WHERE run_id = :run"},
            ],
        }


@app.post("/ask")
def ask(body: Ask):
    try:
        import agent
        return agent.ask(body.question)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"ask not available yet: {e}")


@app.get("/decisions")
def decisions():
    p = OUT / "decisions.json"
    if not p.exists():
        return {"decisions": []}
    return {"decisions": json.loads(p.read_text())}


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}
