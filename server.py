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
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()
OUT = Path("out")
OUT.mkdir(exist_ok=True)

app = FastAPI(title="Alpine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
WEB = Path(__file__).parent / "web"


@app.on_event("startup")
def _ensure_runs():
    """A fresh deploy has no out/: simulate the four scenarios once (about 5 s) so /report works immediately."""
    if os.environ.get("ALPINE_AUTORUN", "1") == "1" and not list(OUT.glob("run_*.json")):
        import thermal
        for sc in thermal.CFG["scenarios"]:
            thermal.run(scenario=sc, push=False)


@app.get("/")
def index():
    """The web page, served from the API so one URL does everything (the page talks to same-origin by default)."""
    return FileResponse(WEB / "index.html")


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


def _runs() -> list[dict]:
    out = []
    for p in sorted(OUT.glob("run_*.json")):
        d = json.loads(p.read_text())
        out.append({"run_id": d["run_id"], "scenario": d["scenario"], "computed_at": d["computed_at"],
                    "policy": d.get("policy", {}), "summary": d["summary"]})
    return out


@app.get("/runs")
def runs():
    """Every local run, newest last. The page uses the latest per scenario."""
    return {"runs": _runs()}


@app.get("/events")
def events(run_id: str, daily: bool = True):
    """Daily (or hourly) aggregates of one run's events, for the charts."""
    import pandas as pd
    p = OUT / f"events_{run_id}.csv"
    if not p.exists():
        raise HTTPException(404, f"no events for {run_id}")
    ev = pd.read_csv(p)
    num = ev[ev.name != "decision"].copy()
    num["value"] = num["value"].astype(float)
    key = "day" if daily else "hour"
    wide = num.pivot_table(index=key, columns="name", values="value", aggfunc="sum")
    if daily:  # state-of-charge is a level, not a flow: take the day's last value
        soc = num[num.name == "storage_soc"].groupby("day")["value"].last()
        wide["storage_soc"] = soc
    wide["dry_mw"] = wide["heat_rejected_mw"] - wide["heat_tower_mw"]
    scen = ev.groupby(key)["scenario"].agg(lambda s: next((x for x in s if x != "normal"), "normal"))
    wide["scenario"] = scen
    cols = ["heat_available_mw", "heat_exported_mw", "heat_stored_mw", "heat_tower_mw", "dry_mw",
            "water_gal", "power_kwh", "it_kwh", "cost_usd", "co2_t", "storage_soc", "scenario"]
    rows = wide[cols].reset_index().round(3).to_dict(orient="records")
    return {"run_id": run_id, "key": key, "rows": rows}


@app.get("/compare")
def compare(scenario: str = "normal"):
    """The same year twice: towers on the chiller condensers (default) vs. dry condensers (prefer_dry).
    This is the agent's water-vs-cost lever."""
    import thermal
    a = thermal.run(scenario=scenario, push=False)["summary"]
    b = thermal.run(scenario=scenario, push=False, policy={"prefer_dry": True})["summary"]
    keys = ["pue", "wue_l_per_kwh", "erf", "water_mgal", "cost_musd", "co2_kt", "hours_tower", "share_reused"]
    return {"scenario": scenario, "policy_b": {"prefer_dry": True},
            "rows": [{"metric": k, "default": a[k], "prefer_dry": b[k],
                      "delta_pct": (b[k] - a[k]) / a[k] * 100 if a[k] else None} for k in keys]}


@app.get("/sensitivity")
def sensitivity(scenario: str = "normal"):
    """One-at-a-time +/-25% on the 'assumption - verify' constants; which ones move WUE and ERF."""
    import copy
    import thermal
    base = thermal.run(scenario=scenario, push=False)["summary"]
    knobs = [("cooling", "dry_cooler_capacity_mw"), ("cooling", "water_gal_per_kwh"),
             ("heat_network", "route_km"), ("heat_network", "heat_price_usd_per_mwh"),
             ("storage", "capacity_mwh"), ("storage", "max_charge_mw"), ("prices", "electricity_usd_per_mwh")]
    rows = []
    orig = copy.deepcopy(thermal.CFG)
    try:
        for sect, k in knobs:
            res = {"knob": f"{sect}.{k}", "base": orig[sect][k]}
            for lab, f in (("low", 0.75), ("high", 1.25)):
                thermal.CFG[sect][k] = orig[sect][k] * f
                s = thermal.run(scenario=scenario, push=False)["summary"]
                res[f"wue_{lab}_pct"] = (s["wue_l_per_kwh"] - base["wue_l_per_kwh"]) / base["wue_l_per_kwh"] * 100
                res[f"erf_{lab}_pct"] = (s["erf"] - base["erf"]) / base["erf"] * 100
                res[f"cost_{lab}_pct"] = (s["cost_musd"] - base["cost_musd"]) / base["cost_musd"] * 100
                thermal.CFG[sect][k] = orig[sect][k]
            rows.append(res)
    finally:
        thermal.CFG.clear(); thermal.CFG.update(orig)
    return {"scenario": scenario, "base": base, "rows": rows}


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}
