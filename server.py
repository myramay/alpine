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
from typing import Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()
import logging  # noqa: E402
import guards  # noqa: E402
import paths  # noqa: E402  (after load_dotenv: DATA_DIR may come from .env)

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
if paths.PERSISTENT:   # a fresh volume gets the repo's shipped runs, report cache and example sites
    _seeded = paths.seed()
    logging.getLogger("alpine").info(json.dumps({"event": "seed", "data_dir": str(paths.DATA_DIR), "copied": _seeded}))
OUT = paths.OUT_DIR                              # local runs; "out" (cwd) when DATA_DIR is unset
OUT.mkdir(parents=True, exist_ok=True)

ROOT = Path(__file__).resolve().parent
RUNS_DIR = paths.RUNS_DIR                        # Keewano runs: <run_id>.csv, index.json, decisions_<id>.json
REPORT_CACHE_PATH = paths.REPORT_CACHE_PATH
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
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins or ["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(guards.GuardMiddleware)   # outermost: access log, body cap, per-IP rate limits
WEB = Path(__file__).parent / "web"


@app.on_event("startup")
def _ensure_runs():
    """A fresh deploy has no out/: simulate the four scenarios once (about 5 s) so /report works immediately."""
    if os.environ.get("ALPINE_AUTORUN", "1") == "1" and not list(OUT.glob("run_*.json")):
        import thermal
        if _rebuild_keewano_runs(thermal):
            return
        for sc in thermal.CFG["scenarios"]:
            thermal.run(scenario=sc, push=False)


def _rebuild_keewano_runs(thermal) -> bool:
    """Re-simulate the runs that are already in Keewano (outputs/runs/index.json, latest Alpine run per
    scenario) under their own run_ids, so the page's runs and the live /report queries are the same runs.
    The sim is deterministic; the agent run gets its policy back from decisions_<run_id>.json."""
    try:
        idx = json.loads((RUNS_DIR / "index.json").read_text())
    except (FileNotFoundError, ValueError):
        return False
    import sites
    latest: dict[tuple[str, str], dict] = {}
    for r in idx:
        if r.get("sim") == "alpine":
            latest[(r.get("site") or "lansing", r["scenario"])] = r  # append-ordered: newest last wins
    if not latest:
        return False
    for (site, sc), r in latest.items():
        try:
            sites.check_id(site)
        except ValueError:
            continue
        policy_by_day, notes = None, None
        if sc == "agent":
            try:
                doc = json.loads((RUNS_DIR / f"decisions_{r['run_id']}.json").read_text())
            except (FileNotFoundError, ValueError):
                continue
            changed = [d for d in doc.get("days", []) if d.get("changed")]
            policy_by_day = {d["day"]: d["policy"] for d in changed}
            notes = {d["day"]: d["reason"][:256] for d in changed}
        thermal.run(scenario=sc, push=False, run_id=r["run_id"], policy_by_day=policy_by_day,
                    policy_notes=notes, site=site)
    return True


@app.get("/site.js")
def site_js():
    return FileResponse(WEB / "site.js", media_type="application/javascript")


@app.get("/site-light.js")
def site_light_js():
    return FileResponse(WEB / "site-light.js", media_type="application/javascript")


@app.get("/skyline.js")
def skyline_js():
    return FileResponse(WEB / "skyline.js", media_type="application/javascript")


@app.get("/geo/{name}")
def geo(name: str):
    """User-built sites' maps live in DATA_DIR/geo; the repo's web/geo has lansing + shipped examples."""
    import sites
    for d in dict.fromkeys([sites.GEO_DIR, WEB / "geo"]):
        p = (d / name).resolve()
        if p.parent == d.resolve() and p.is_file():
            return FileResponse(p, media_type="application/json")
    raise HTTPException(404)


@app.get("/vendor/{name}")
def vendor(name: str):
    p = (WEB / "vendor" / name).resolve()
    if p.parent != (WEB / "vendor").resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="text/css" if name.endswith(".css") else "application/javascript")


@app.get("/")
def index():
    """The production page (light design): Site, Report, Pipeline, Design, Ask, Decisions, Model.
    Served from the API so one URL does everything (the page talks to same-origin by default)."""
    return FileResponse(WEB / "light.html")


@app.get("/classic")
def classic():
    """The original dark page (web/index.html), kept with all its features."""
    return FileResponse(WEB / "index.html")


@app.get("/light")
def light():
    """Same as / (the light page); kept so old links keep working."""
    return FileResponse(WEB / "light.html")


class Ask(BaseModel):
    question: str
    run_id: Optional[str] = None  # Optional, not "str | None": pydantic evaluates it, and Python 3.9 cannot
    site: Optional[str] = None    # default run = that site's latest normal run


def _site(site: Optional[str]) -> str:
    """A validated site id (default lansing); 404 for unknown or unfinished sites."""
    import sites
    try:
        return sites.check_id(site)
    except ValueError as e:
        raise HTTPException(404, str(e))


def _latest_run() -> dict | None:
    runs = sorted(OUT.glob("run_*.json"))
    return json.loads(runs[-1].read_text()) if runs else None


def _run_job(run_id: str, scenario: str, push: bool, sim: str, site: str = "lansing") -> None:
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
            r = thermal.run(scenario=scenario, push=push, run_id=run_id, save=True, site=site)
            JOBS[run_id].update(events=r["n_events"], sent=r["n_sent"], summary=r["summary"])
        JOBS[run_id].update(status="done", finished=_now())
    except Exception as e:  # noqa: BLE001
        JOBS[run_id].update(status="error", error=str(e)[:500], finished=_now())


@app.post("/run")
def run(background: BackgroundTasks, scenario: str = "normal", push: bool = True, wait: bool = True,
        sim: str = "alpine", site: Optional[str] = None):
    """wait=true (default): simulate (and push) now and return the summary. wait=false: queue it as a
    background job and poll /jobs. sim=basic uses the first-build sim (thermal_basic.py, Lansing only).
    site: a sites.py id (default lansing)."""
    site = _site(site)
    if not wait or sim == "basic":
        run_id = f"{scenario}-{uuid.uuid4().hex[:6]}"
        JOBS[run_id] = {"run_id": run_id, "scenario": scenario, "sim": sim, "site": site, "status": "queued",
                        "started": _now()}
        background.add_task(_run_job, run_id, scenario, push, sim, site)
        return {"run_id": run_id, "site": site, "status": "queued"}
    import thermal
    result = thermal.run(scenario=scenario, push=push, save=True, site=site)
    return {"run_id": result["run_id"], "site": site, "events": result["n_events"], "sent": result["n_sent"],
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
            paths.write_text_atomic(REPORT_CACHE_PATH, json.dumps(_report_cache, indent=1, default=str))
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
        run_id = body.run_id
        if not run_id and body.site:
            run_id = agent.latest_run("normal", sim="alpine", site=_site(body.site))
        return agent.ask(body.question, run_id=run_id)
    except Exception as e:  # noqa: BLE001
        detail = str(e)
        subs = getattr(e, "exceptions", None)   # ExceptionGroup from the MCP client: show the real cause
        if subs:
            detail += " :: " + "; ".join(f"{type(s).__name__}: {str(s)[:200]}" for s in subs)
        raise HTTPException(503, f"ask failed: {detail}")


@app.get("/decisions")
def decisions(run_id: str | None = None):
    """{"decisions": [{day, question, answer, decision, changed}]} for the page, plus "detail": the full
    log (every query string and answer) for the agent run (latest if run_id is not given)."""
    p = OUT / "decisions.json"
    flat = json.loads(p.read_text()) if p.exists() else []
    if run_id:
        dp = RUNS_DIR / f"decisions_{run_id}.json"
    else:
        import agent
        # Newest Alpine-sim agent run by index.json "created" (file mtimes are meaningless after a
        # git checkout on Railway, which made the page pick the old thermal_basic run).
        latest = agent.latest_run(scenario="agent", sim="alpine") or agent.latest_run(scenario="agent")
        dp = RUNS_DIR / f"decisions_{latest}.json" if latest else None
        if not (dp and dp.exists()):
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


def _runs(site: str | None = None) -> list[dict]:
    out = []
    for p in sorted(OUT.glob("run_*.json")):
        d = json.loads(p.read_text())
        s = d.get("site") or "lansing"
        if site and s != site:
            continue
        out.append({"run_id": d["run_id"], "scenario": d["scenario"], "site": s, "computed_at": d["computed_at"],
                    "policy": d.get("policy", {}), "summary": d["summary"]})
    return out


@app.get("/runs")
def runs(site: Optional[str] = None):
    """Every local run of the site (default lansing), newest last. The page uses the latest per scenario.
    "keewano_runs": that site's runs that were pushed to Keewano (outputs/runs/index.json; entries
    without a "site" are Lansing's)."""
    site = _site(site)
    try:
        import agent
        kw = [r for r in agent.load_index() if (r.get("site") or "lansing") == site]
    except Exception:  # noqa: BLE001
        kw = []
    return {"site": site, "runs": _runs(site), "keewano_runs": kw}


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
    wide["dry_mw"] = wide["heat_dry_mw"]
    scen = ev.groupby(key)["scenario"].agg(lambda s: next((x for x in s if x != "normal"), "normal"))
    wide["scenario"] = scen
    cols = ["heat_available_mw", "heat_exported_mw", "heat_stored_mw", "heat_tower_mw", "dry_mw",
            "water_gal", "power_kwh", "it_kwh", "cost_usd", "co2_t", "storage_soc", "scenario"]
    rows = wide[cols].reset_index().round(3).to_dict(orient="records")
    return {"run_id": run_id, "key": key, "rows": rows}


@app.get("/compare")
def compare(scenario: str = "normal", site: Optional[str] = None):
    """The same year twice: towers on the chiller condensers (default) vs. dry condensers (prefer_dry).
    This is the agent's water-vs-cost lever."""
    import thermal
    site = _site(site)
    a = thermal.run(scenario=scenario, push=False, save=False, site=site)["summary"]
    b = thermal.run(scenario=scenario, push=False, policy={"prefer_dry": True}, save=False, site=site)["summary"]
    keys = ["pue", "wue_l_per_kwh", "erf", "water_mgal", "cost_musd", "co2_kt", "hours_tower", "share_reused"]
    return {"scenario": scenario, "policy_b": {"prefer_dry": True},
            "rows": [{"metric": k, "default": a[k], "prefer_dry": b[k],
                      "delta_pct": (b[k] - a[k]) / a[k] * 100 if a[k] else None} for k in keys]}


_design_cache: dict[str, dict] = {}   # site id -> {"rec", "at"}


def _design(site: str, refresh: bool = False) -> dict:
    import design
    with _lock:
        hit = _design_cache.get(site)
    if refresh or not hit:
        hit = {"rec": design.recommend(site=site), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with _lock:
            _design_cache[site] = hit
    return hit


@app.get("/design")
def design_recommend(refresh: bool = False, site: Optional[str] = None):
    """How this site should reuse its heat: ranked pathways (network to real buildings, trucked storage,
    co-located greenhouse / aquaculture / drying), the network route and methods, pit location, and a
    plain-English recommendation. Computed from the site's customers.csv + config/design.yaml (~1-2 s,
    cached per site). site: a sites.py id (default lansing)."""
    site = _site(site)
    hit = _design(site, refresh)
    return {**hit["rec"], "computed_at": hit["at"]}


@app.get("/design/geojson")
def design_geojson(site: Optional[str] = None):
    """Map layer for /design: the site, every building cluster (connected or not, which shore) and pipe segments."""
    import design
    site = _site(site)
    return design.geojson(_design(site)["rec"], site=site)


# ------------------------------------------------------------------------------------ sites

class SiteIn(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None     # an address, or "lat, lon"
    lat: Optional[float] = None
    lon: Optional[float] = None
    it_mw: Optional[float] = 100.0


MAX_BUILDS = 2


def _clean(text: Optional[str], n: int, what: str) -> Optional[str]:
    if text is None:
        return None
    text = " ".join(str(text).split())
    if len(text) > n:
        raise HTTPException(422, f"{what} is too long (max {n} characters)")
    if any(ord(ch) < 32 for ch in text):
        raise HTTPException(422, f"{what} has control characters")
    return text or None


def _headline(meta: dict) -> dict | None:
    """Lansing's headline comes from the design cache (computed once); built sites store theirs."""
    if meta.get("id") != "lansing":
        return meta.get("headline")
    try:
        rec = _design("lansing")["rec"]
    except Exception:  # noqa: BLE001
        return None
    import sites
    return sites.headline_of(rec)


def _site_view(meta: dict, full: bool = False) -> dict:
    keys = ("id", "name", "address", "display_name", "lat", "lon", "it_mw", "created", "status", "step",
            "progress", "ready_note", "error", "failed_step", "example", "run_id", "started", "finished")
    out = {k: meta.get(k) for k in keys if k in meta}
    out["headline"] = _headline(meta) if meta.get("status") == "ready" else None
    if full:
        out.update({k: meta.get(k) for k in ("steps", "sources", "notes", "facts", "keewano", "sim", "radius_km",
                                              "seconds", "updated") if k in meta})
        out.setdefault("steps", __import__("sites").STEPS)
    return out


@app.post("/sites")
def create_site(body: SiteIn):
    """Model a new location: {address | "lat, lon" | lat+lon, name?, it_mw (1-1000, default 100)}. Builds in
    the background (geocoding -> weather -> buildings -> demand -> simulating -> streaming to Keewano ->
    designing -> ready); poll GET /sites/{site_id}."""
    import sites
    name = _clean(body.name, 80, "name")
    address = _clean(body.address, 200, "address")
    it_mw = 100.0 if body.it_mw is None else float(body.it_mw)
    if not 1 <= it_mw <= 1000:
        raise HTTPException(422, "it_mw must be between 1 and 1000")
    if (body.lat is None) != (body.lon is None):
        raise HTTPException(422, "give both lat and lon, or neither")
    if body.lat is not None and not (-90 <= body.lat <= 90 and -180 <= body.lon <= 180):
        raise HTTPException(422, "lat must be -90..90 and lon -180..180")
    if body.lat is None and not (address or name):
        raise HTTPException(422, "give an address (or \"lat, lon\"), or lat and lon")
    building = sum(1 for m in sites.list_sites() if m.get("status") in ("queued", "building"))
    if building >= MAX_BUILDS:
        raise HTTPException(429, "two sites are already building; try again in a minute")
    try:
        sid = sites.start_site(name=name, address=address, lat=body.lat, lon=body.lon, it_mw=it_mw)
    except ValueError as e:
        raise HTTPException(422, str(e)[:200])
    return {"site_id": sid, "status": "queued"}


@app.post("/sites/{site_id}/retry")
def retry_site(site_id: str):
    """Rebuild a site whose build failed (e.g. Overpass was down); cached downloads are reused."""
    import sites
    if not sites.ID_RE.match(site_id or ""):
        raise HTTPException(404, "no such site")
    try:
        sites.retry_site(site_id)
    except ValueError as e:
        raise HTTPException(409, str(e)[:200])
    return {"site_id": site_id, "status": "queued"}


@app.get("/sites")
def list_sites():
    """Every site, Lansing (the worked example) first: id, name, status, created, headline numbers."""
    import sites
    return {"sites": [_site_view(m) for m in sites.list_sites()]}


@app.get("/sites/{site_id}")
def get_site(site_id: str):
    """One site's meta: status/step/progress while building, then sources, notes, Keewano push, headline."""
    import sites
    meta = sites.read_meta(site_id) if sites.ID_RE.match(site_id or "") else None
    if not meta:
        raise HTTPException(404, f"no site {site_id!r}")
    return _site_view(meta, full=True)


# ------------------------------------------------------------- assumptions, re-run, exports

def _invalidate(site: str) -> None:
    """Overrides changed: drop every cached result for the site."""
    import design
    design._cfgs_for.cache_clear()
    with _lock:
        _design_cache.pop(site, None)
        for k in [k for k in _SENS_CACHE if k[0] == site]:
            _SENS_CACHE.pop(k, None)


def _changes(site: str, before: dict, after: dict) -> dict:
    """Headline before/after plus every summary and pathway number that moved."""
    import assumptions
    import sites
    hb, ha = sites.headline_of(before), sites.headline_of(after)
    changed = []
    for k in ("heat_available_gwh", "heat_reused_gwh", "share_reused", "npv_recommended_usd", "co2_avoided_t"):
        a, b = before["summary"].get(k), after["summary"].get(k)
        if a != b:
            changed.append({"what": f"summary.{k}", "before": a, "after": b})
    pb = {p["key"]: p for p in before["pathways"]}
    for p in after["pathways"]:
        q = pb.get(p["key"], {})
        for k in ("verdict", "delivered_mwh", "npv_usd", "npv_with_carbon_usd", "co2_avoided_t"):
            if q.get(k) != p.get(k):
                changed.append({"what": f"{p['key']}.{k}", "label": p["label"], "before": q.get(k), "after": p.get(k)})
    if site != "lansing":     # the site chip / compare row show the stored headline
        sites._write_meta(site, headline=ha)
    return {"site": site, "before": hb, "after": ha, "changed": changed,
            "overrides": assumptions.load_overrides(site), "assumptions": assumptions.grouped(site)}


@app.get("/sites/{site_id}/assumptions")
def get_assumptions(site_id: str):
    """Every assumption that moves this site's plan, grouped: value, default, unit, range, source, verify flag."""
    import assumptions
    return assumptions.grouped(_site(site_id))


@app.put("/sites/{site_id}/assumptions")
def put_assumptions(site_id: str, body: dict):
    """{key: value, ...} (null resets one key). Validated (numeric, within range), saved to the site's
    overrides, then /design is recomputed: returns the headline before/after and every number that moved."""
    import assumptions
    site = _site(site_id)
    before = _design(site)["rec"]
    try:
        assumptions.save(site, body)
    except ValueError as e:
        raise HTTPException(422, str(e)[:1000])
    _invalidate(site)
    return _changes(site, before, _design(site, refresh=True)["rec"])


@app.delete("/sites/{site_id}/assumptions")
def delete_assumptions(site_id: str):
    """Back to the defaults in site.yaml / design.yaml; recomputes /design."""
    import assumptions
    site = _site(site_id)
    before = _design(site)["rec"]
    assumptions.reset(site)
    _invalidate(site)
    return _changes(site, before, _design(site, refresh=True)["rec"])


def _rerun_job(run_id: str, site: str) -> None:
    _run_job(run_id, "normal", True, "alpine", site)
    job = JOBS.get(run_id, {})
    if job.get("status") == "done":
        if site != "lansing":
            import sites
            sites._write_meta(site, run_id=run_id, keewano={"accepted": job.get("sent"), "events": job.get("events"),
                                                             "rerun": True})
        _invalidate(site)


@app.post("/sites/{site_id}/rerun")
def rerun_site(site_id: str, background: BackgroundTasks):
    """Re-simulate the normal year with the site's current assumptions and stream it to Keewano (background,
    ~2 min). Poll GET /jobs (kind "rerun")."""
    site = _site(site_id)
    run_id = f"normal-{datetime.now(timezone.utc):%m%d%H%M}-{uuid.uuid4().hex[:4]}"
    JOBS[run_id] = {"run_id": run_id, "scenario": "normal", "sim": "alpine", "site": site, "kind": "rerun",
                    "status": "queued", "started": _now()}
    background.add_task(_rerun_job, run_id, site)
    return {"run_id": run_id, "site": site, "status": "queued"}


def _export(site_id: str) -> tuple[str, dict, dict | None]:
    import sites
    site = _site(site_id)
    hit = _design(site)
    return site, {**hit["rec"], "computed_at": hit["at"]}, sites.read_meta(site)


def _file(body: str, media: str, name: str, inline: bool = False):
    from fastapi.responses import Response
    disp = "inline" if inline else "attachment"
    return Response(body, media_type=media, headers={"Content-Disposition": f'{disp}; filename="{name}"'})


@app.get("/sites/{site_id}/plan.html")
def plan_html(site_id: str, download: bool = False):
    """Printable plan report: summary, recommendation, pathways, monthly balance, network breakeven,
    assumptions (source, verify, overridden), data sources, methodology."""
    import exports
    site, rec, meta = _export(site_id)
    return _file(exports.plan_html(site, rec, meta), "text/html; charset=utf-8", exports.filename(site, "html"),
                 inline=not download)


@app.get("/sites/{site_id}/plan.json")
def plan_json(site_id: str):
    """The full /design result + assumptions + provenance, for scripts."""
    import exports
    site, rec, meta = _export(site_id)
    return _file(json.dumps(exports.plan_bundle(site, rec, meta), indent=1, default=str), "application/json",
                 exports.filename(site, "json"))


@app.get("/sites/{site_id}/pathways.csv")
def pathways_csv(site_id: str):
    import exports
    site, rec, _ = _export(site_id)
    return _file(exports.pathways_csv(rec), "text/csv; charset=utf-8", exports.filename(site, "csv", "pathways"))


@app.get("/sites/{site_id}/assumptions.csv")
def assumptions_csv(site_id: str):
    import exports
    site = _site(site_id)
    return _file(exports.assumptions_csv(site), "text/csv; charset=utf-8", exports.filename(site, "csv", "assumptions"))


_SENS_CACHE: dict = {}


@app.get("/sensitivity")
def sensitivity(scenario: str = "normal", site: Optional[str] = None):
    """Cached per (site, scenario): the inputs (config + data) don't change while the server runs, and a cold run is ~6 s."""
    key = (_site(site), scenario)
    if key not in _SENS_CACHE:
        _SENS_CACHE[key] = _sensitivity(scenario, site)
    return _SENS_CACHE[key]


def _sensitivity(scenario: str = "normal", site: Optional[str] = None):
    """One-at-a-time +/-25% on the 'assumption - verify' constants; which ones move WUE and ERF."""
    import copy
    import thermal
    site = _site(site)
    if site != "lansing":
        return _sensitivity_site(scenario, site)
    base = thermal.run(scenario=scenario, push=False, save=False)["summary"]
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
                s = thermal.run(scenario=scenario, push=False, save=False)["summary"]
                res[f"wue_{lab}_pct"] = (s["wue_l_per_kwh"] - base["wue_l_per_kwh"]) / base["wue_l_per_kwh"] * 100
                res[f"erf_{lab}_pct"] = (s["erf"] - base["erf"]) / base["erf"] * 100
                res[f"cost_{lab}_pct"] = (s["cost_musd"] - base["cost_musd"]) / base["cost_musd"] * 100
                thermal.CFG[sect][k] = orig[sect][k]
            rows.append(res)
    finally:
        thermal.CFG.clear(); thermal.CFG.update(orig)
    return {"scenario": scenario, "base": base, "rows": rows}


SENS_KNOBS = [("cooling", "dry_cooler_capacity_mw"), ("cooling", "water_gal_per_kwh"),
              ("heat_network", "route_km"), ("heat_network", "heat_price_usd_per_mwh"),
              ("storage", "capacity_mwh"), ("storage", "max_charge_mw"), ("prices", "electricity_usd_per_mwh")]


def _sensitivity_site(scenario: str, site: str) -> dict:
    """/sensitivity for a sites.py site: the same knobs on a copy of its config (thermal.CFG untouched)."""
    import copy
    import thermal
    orig = thermal.site_cfg(site)
    base = thermal.run(scenario=scenario, push=False, save=False, site=site)["summary"]
    rows = []
    for sect, k in SENS_KNOBS:
        res = {"knob": f"{sect}.{k}", "base": orig[sect][k]}
        for lab, f in (("low", 0.75), ("high", 1.25)):
            cfg = copy.deepcopy(orig)
            cfg[sect][k] = orig[sect][k] * f
            s = thermal.run(scenario=scenario, push=False, save=False, site=site, cfg=cfg)["summary"]
            for m, key in (("wue", "wue_l_per_kwh"), ("erf", "erf"), ("cost", "cost_musd")):
                res[f"{m}_{lab}_pct"] = (s[key] - base[key]) / base[key] * 100 if base[key] else 0.0
        rows.append(res)
    return {"scenario": scenario, "site": site, "base": base, "rows": rows}


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat(),
            "env": {k: bool(os.getenv(k)) for k in ENV_VARS},
            "data_dir": str(paths.DATA_DIR), "persistent": paths.PERSISTENT, "writable": paths.writable()}
