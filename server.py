"""Alpine API. /run, /ask, /report, /decisions, /runs, /events, /compare, /sensitivity, /jobs, /health.

/report asks Keewano live (agent.report); if Keewano fails it serves the last live result for that
run (outputs/report_cache.json, "cached": true), and failing that the last local run's numbers from out/.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()
OUT = Path("out")
OUT.mkdir(exist_ok=True)

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "outputs" / "runs"            # Keewano runs: <run_id>.csv, index.json, decisions_<id>.json
REPORT_CACHE_PATH = ROOT / "outputs" / "report_cache.json"
ENV_VARS = ["ANTHROPIC_API_KEY", "KEEWANO_API_KEY", "KEEWANO_PAT", "KEEWANO_MCP_URL",
            "KEEWANO_EMAIL", "KEEWANO_PROJECT_ID"]
_lock = threading.Lock()
JOBS: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_report_cache() -> dict:
    try:
        return json.loads(REPORT_CACHE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_report_cache: dict[str, dict] = _load_report_cache()

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
    run_id: str | None = None


def _latest_run() -> dict | None:
    runs = sorted(OUT.glob("run_*.json"))
    return json.loads(runs[-1].read_text()) if runs else None


def _run_job(run_id: str, scenario: str, push: bool, sim: str) -> None:
    JOBS[run_id].update(status="simulating")
    try:
        if sim == "basic":
            import keewano
            import thermal_basic
            r = thermal_basic.simulate(scenario=scenario, run_id=run_id)
            JOBS[run_id].update(status="saving", path=str(thermal_basic.save_run(r)))
            if push:
                JOBS[run_id].update(status="pushing")
                JOBS[run_id].update(push=keewano.send_run(r))
        else:
            import thermal
            r = thermal.run(scenario=scenario, push=push, run_id=run_id, save=True)
            JOBS[run_id].update(events=r["n_events"], sent=r["n_sent"], summary=r["summary"])
        JOBS[run_id].update(status="done", finished=_now())
    except Exception as e:  # noqa: BLE001
        JOBS[run_id].update(status="error", error=str(e)[:500], finished=_now())


@app.post("/run")
def run(background: BackgroundTasks, scenario: str = "normal", push: bool = True, wait: bool = True,
        sim: str = "alpine"):
    """wait=true (default): simulate (and push) now and return the summary. wait=false: queue it as a
    background job and poll /jobs. sim=basic uses the first-build sim (thermal_basic.py)."""
    if not wait or sim == "basic":
        run_id = f"{scenario}-{uuid.uuid4().hex[:6]}"
        JOBS[run_id] = {"run_id": run_id, "scenario": scenario, "sim": sim, "status": "queued", "started": _now()}
        background.add_task(_run_job, run_id, scenario, push, sim)
        return {"run_id": run_id, "status": "queued"}
    import thermal
    result = thermal.run(scenario=scenario, push=push, save=True)
    return {"run_id": result["run_id"], "events": result["n_events"], "sent": result["n_sent"],
            "summary": result["summary"]}


@app.get("/jobs")
def jobs():
    return {"jobs": list(JOBS.values())}


@app.get("/report")
def report(run_id: str | None = None):
    """Three metrics, each the result of one Keewano query. Falls back to the last live result for the
    run, then to the last local run."""
    try:
        import agent
        live = agent.report(run_id)
        with _lock:
            _report_cache[live["run_id"]] = live
            REPORT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPORT_CACHE_PATH.write_text(json.dumps(_report_cache, indent=1, default=str))
        return live
    except Exception as e:  # noqa: BLE001 - Keewano slow/unreachable: cached, then local
        key = run_id
        if key is None:
            try:
                import agent
                key = agent.latest_run("normal")
            except Exception:  # noqa: BLE001
                key = None
        if key and key in _report_cache:
            return {**_report_cache[key], "cached": True, "error": str(e)[:300]}
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
        return agent.ask(body.question, run_id=body.run_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"ask not available yet: {e}")


@app.get("/decisions")
def decisions(run_id: str | None = None):
    """{"decisions": [{day, question, answer, decision, changed}]} for the page, plus "detail": the full
    log (every query string and answer) for the agent run (latest if run_id is not given)."""
    p = OUT / "decisions.json"
    flat = json.loads(p.read_text()) if p.exists() else []
    if run_id:
        dp = RUNS_DIR / f"decisions_{run_id}.json"
    else:
        files = sorted(RUNS_DIR.glob("decisions_*.json"), key=lambda f: f.stat().st_mtime)
        dp = files[-1] if files else None
    detail = json.loads(dp.read_text()) if dp and dp.exists() else None
    if detail and (run_id or not flat):  # page shape from the detailed log
        flat = [{"day": d["day"], "question": " / ".join(q["question"] for q in d.get("questions", [])),
                 "answer": ", ".join(f"{q['answer']:,.0f}" if isinstance(q.get("answer"), (int, float))
                                     else str(q.get("answer")) for q in d.get("questions", [])),
                 "decision": d.get("reason", ""), "changed": d.get("changed", False)}
                for d in detail.get("days", []) if d.get("day", 0) > 0]
    return {"decisions": flat, "run_id": detail.get("agent_run_id") if detail else None, "detail": detail}


def _runs() -> list[dict]:
    out = []
    for p in sorted(OUT.glob("run_*.json")):
        d = json.loads(p.read_text())
        out.append({"run_id": d["run_id"], "scenario": d["scenario"], "computed_at": d["computed_at"],
                    "policy": d.get("policy", {}), "summary": d["summary"]})
    return out


@app.get("/runs")
def runs():
    """Every local run, newest last. The page uses the latest per scenario.
    "keewano_runs": the runs that were pushed to Keewano (outputs/runs/index.json)."""
    try:
        import agent
        kw = agent.load_index()
    except Exception:  # noqa: BLE001
        kw = []
    return {"runs": _runs(), "keewano_runs": kw}


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
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat(),
            "env": {k: bool(os.getenv(k)) for k in ENV_VARS}}
