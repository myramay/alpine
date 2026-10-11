"""Thermal Layer agents: the daily policy agent and the /ask question answerer.

Daily policy agent (`python agent.py --run <baseline_run_id>`)
---------------------------------------------------------------
Keewano takes about 5 minutes to make new events queryable, so an agent cannot
simulate a day, push it, and query it before deciding the next day. We do not pretend
otherwise. The loop is a replay over a run that is already in Keewano:

  1. A baseline run (scenario "normal", default policy) is simulated, pushed, and
     ingested in Keewano ahead of time.
  2. The agent asks Keewano three fixed questions about every day of that baseline,
     as three SQL queries grouped by day (`WHERE run_id = '<id>' GROUP BY day`):
       - water used per day (sum(water_gal))
       - hours with unmet heat demand per day (unmet_demand_kw > 0)
       - hours the storage pit was empty per day (storage_soc_pct <= 5)
     The exact query strings are kept with every answer.
  3. For each day d (1..364) it applies rules to day d-1's answers to pick the Policy
     for day d:
       - water above the threshold (top quartile of baseline daily water by default,
         or --water-gal) -> raise dry_boost by 0.1 (max 0.3) to move heat from the
         towers to the dry coolers; otherwise decay dry_boost by 0.05.
       - unmet demand > 0 h -> precharge=True (keep the pit fuller so it can cover the
         next peak) and keep the discharge floor at its minimum, because a higher
         floor would lock away heat we just ran short of.
       - pit empty > 6 h -> precharge=True, nothing else.
       - precharge switches off again after 3 calm days in a row.
     Each day gets a one-line reason.
  4. Every day's {day, date, questions:[{question, query, answer}], policy, reason,
     changed} goes to outputs/runs/decisions_<agent_run_id>.json. Only days whose
     policy changed get a policy_note (emitted as a policy_change event).
  5. The year is re-simulated with scenario="agent" using those policies and notes,
     saved, pushed with keewano.send_run (unless --no-push), and baseline vs agent
     totals (water, power, cost, unmet) are printed from the local hourly data.

So the decisions are driven by Keewano answers about the baseline year, and the
agent's own run lands in Keewano a few minutes later, where /ask and /report can
compare the two.

--offline computes the same three answers from outputs/runs/<run_id>.csv instead of
Keewano (for building and testing before keys exist); the log says "offline".

/ask (`ask(question, run_id=None)`)
-----------------------------------
A Claude tool-use loop. Tools: every tool on the Keewano MCP server (discovered at
runtime) plus a local `run_sql` that calls keewano.sql so the exact query and rows are
captured. Returns {answer, query, rows, tool_calls}.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import os
import sys
import uuid
from dataclasses import asdict, replace
from pathlib import Path

import schema
from schema import Policy

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "outputs" / "runs"
OUT_DIR = ROOT / "out"  # Alpine's local run files (thermal.run) and decisions.json for the web page
INDEX_PATH = RUNS_DIR / "index.json"
QUESTIONS_PATH = ROOT / "questions.md"

ASK_MODEL = "claude-sonnet-5-5"
ASK_MAX_ITERS = 6

DRY_BOOST_MAX = 0.3
DRY_BOOST_STEP = 0.1
DRY_BOOST_DECAY = 0.05
EMPTY_HOURS_LIMIT = 6
PRECHARGE_CALM_DAYS = 3
BASE_DATE = dt.date(2025, 1, 1)


# --------------------------------------------------------------------------- helpers

def _keewano():
    import keewano  # lazy: written in parallel, needs env only when used
    return keewano


def _thermal():
    import thermal  # Alpine sim (main)
    return thermal


def _thermal_basic():
    import thermal_basic  # first-build sim, kept
    return thermal_basic


def run_sim(run_id: str) -> str:
    """Which sim produced a run: index.json "sim" field; entries without it came from thermal_basic."""
    for r in load_index():
        if r.get("run_id") == run_id:
            return r.get("sim", "basic")
    return "alpine"


def load_index() -> list[dict]:
    try:
        return json.loads(INDEX_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def latest_run(scenario: str | None = None, sim: str | None = None) -> str | None:
    runs = [r for r in load_index() if (scenario is None or r.get("scenario") == scenario)
            and (sim is None or r.get("sim", "basic") == sim)]
    if not runs:
        return None
    runs.sort(key=lambda r: str(r.get("created", "")))
    return runs[-1]["run_id"]


def day_date(day: int) -> str:
    return (BASE_DATE + dt.timedelta(days=day)).isoformat()


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


# ------------------------------------------------------------ the three daily questions

DAILY_QUESTIONS = [
    {
        "key": "water_gal",
        "question": "How many gallons of water did the site use yesterday?",
        "query": ("SELECT day, sum(water_gal) AS water_gal FROM events "
                  "WHERE run_id = '{run_id}' AND day IS NOT NULL GROUP BY day ORDER BY day"),
    },
    {
        "key": "unmet_hours",
        "question": "How many hours yesterday was heat demand higher than heat available?",
        "query": ("SELECT day, count(unmet_demand_kw) FILTER (WHERE unmet_demand_kw > 0) AS unmet_hours "
                  "FROM events WHERE run_id = '{run_id}' AND day IS NOT NULL GROUP BY day ORDER BY day"),
    },
    {
        "key": "empty_hours",
        "question": "How many hours yesterday was the pit at or below its minimum level?",
        "query": ("SELECT day, count(storage_soc_pct) FILTER (WHERE storage_soc_pct <= 5) AS empty_hours "
                  "FROM events WHERE run_id = '{run_id}' AND day IS NOT NULL GROUP BY day ORDER BY day"),
    },
]


def _rows_to_by_day(rows: list[dict], key: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in rows:
        d = r.get("day")
        if d is None:
            continue
        v = r.get(key)
        out[int(float(d))] = float(v) if v is not None else 0.0
    return out


def fetch_daily_answers_keewano(run_id: str) -> dict:
    kw = _keewano()
    answers = {}
    for q in DAILY_QUESTIONS:
        query = q["query"].format(run_id=run_id)
        rows = kw.sql(query)
        answers[q["key"]] = {"question": q["question"], "query": query,
                             "by_day": _rows_to_by_day(rows, q["key"])}
    return {"source": "keewano", "answers": answers}


def load_hourly_csv(run_id: str) -> list[dict]:
    import pandas as pd
    path = RUNS_DIR / f"{run_id}.csv"
    df = pd.read_csv(path)
    return df.to_dict("records")


def _hourly_day(row: dict) -> int:
    if row.get("day") is not None and row.get("day") == row.get("day"):  # not NaN
        return int(row["day"])
    return int(row["hour"]) // 24


def fetch_daily_answers_offline(run_id: str, hourly: list[dict] | None = None) -> dict:
    hourly = hourly if hourly is not None else load_hourly_csv(run_id)
    water: dict[int, float] = {}
    unmet: dict[int, float] = {}
    empty: dict[int, float] = {}
    for row in hourly:
        d = _hourly_day(row)
        water[d] = water.get(d, 0.0) + float(row.get("water_gal", 0) or 0)
        unmet[d] = unmet.get(d, 0.0) + (1 if round(float(row.get("unmet_demand_kw", 0) or 0)) > 0 else 0)
        soc = float(row.get("storage_soc_pct", 100) or 0)
        empty[d] = empty.get(d, 0.0) + (1 if soc <= 5 else 0)
    by_key = {"water_gal": water, "unmet_hours": unmet, "empty_hours": empty}
    answers = {}
    for q in DAILY_QUESTIONS:
        label = f"[offline: computed from outputs/runs/{run_id}.csv, same logic as] "
        answers[q["key"]] = {"question": q["question"],
                             "query": label + q["query"].format(run_id=run_id),
                             "by_day": by_key[q["key"]]}
    return {"source": "offline", "answers": answers}


# ------------------------------------------------------------------------------ rules

def water_threshold(water_by_day: dict[int, float], pct: float = 75.0) -> float:
    vals = sorted(water_by_day.values())
    if not vals:
        return float("inf")
    idx = min(len(vals) - 1, max(0, int(round(pct / 100.0 * (len(vals) - 1)))))
    return vals[idx]


def decide_basic(prev: Policy, water: float, unmet_h: float, empty_h: float, threshold: float,
           calm_streak: int, top_label: str = "top 25%",
           floor_min: float = 0.05) -> tuple[Policy, str, int]:
    """Policy for today from yesterday's three answers. Returns (policy, reason, calm_streak)."""
    p = replace(prev)
    reasons: list[str] = []

    if water > threshold:
        p.dry_boost = round(min(DRY_BOOST_MAX, prev.dry_boost + DRY_BOOST_STEP), 2)
        if p.dry_boost != prev.dry_boost:
            reasons.append(f"yesterday used {_fmt(water)} gal ({top_label}, > {_fmt(threshold)}) "
                           f"→ dry_boost {p.dry_boost:.2f} to cut tower water")
        else:
            reasons.append(f"yesterday used {_fmt(water)} gal ({top_label}) → dry_boost held at max "
                           f"{p.dry_boost:.2f}")
    elif prev.dry_boost > 0:
        p.dry_boost = round(max(0.0, prev.dry_boost - DRY_BOOST_DECAY), 2)
        reasons.append(f"water back to {_fmt(water)} gal (≤ {_fmt(threshold)}) "
                       f"→ dry_boost decays to {p.dry_boost:.2f} to save fan power")

    stressed = unmet_h > 0 or empty_h > EMPTY_HOURS_LIMIT
    if unmet_h > 0:
        p.precharge = True
        p.discharge_floor = floor_min
        reasons.append(f"{unmet_h:.0f} h of unmet heat demand yesterday → precharge on, "
                       f"discharge floor {floor_min:.2f} so the pit can cover the next peak")
    elif empty_h > EMPTY_HOURS_LIMIT:
        p.precharge = True
        reasons.append(f"pit empty {empty_h:.0f} h yesterday (> {EMPTY_HOURS_LIMIT}) → precharge on")

    calm_streak = 0 if stressed else calm_streak + 1
    if prev.precharge and not stressed and calm_streak >= PRECHARGE_CALM_DAYS:
        p.precharge = False
        reasons.append(f"{calm_streak} calm days (no unmet demand, pit not empty) → precharge off")

    if not reasons or p == prev:
        reason = (f"no change: yesterday {_fmt(water)} gal, {unmet_h:.0f} h unmet, "
                  f"{empty_h:.0f} h pit empty")
        if reasons:
            reason += "; " + "; ".join(reasons)
    else:
        reason = "; ".join(reasons)
    return p, reason, calm_streak


# Alpine rules (thermal.py's policy knobs: prefer_dry, storage_reserve, tower_cap_mw).
# Thresholds the rules use on the three answers. Tune after the first run.
WATER_GAL_PER_DAY_HIGH = 200_000
UNMET_HOURS_HIGH = 4
PIT_EMPTY_HOURS_HIGH = 6


def decide(water_gal: float, unmet_hours: int, pit_empty_hours: int, policy: dict,
           water_high: float | None = None) -> tuple[dict, str]:
    """Pure rules on yesterday's answers. Returns (new_policy, reason)."""
    high = WATER_GAL_PER_DAY_HIGH if water_high is None else water_high
    p, why = dict(policy), []
    if water_gal > high:
        p["prefer_dry"] = True
        p["tower_cap_mw"] = min(p.get("tower_cap_mw", 1e9), 20.0)
        why.append(f"water {water_gal:,.0f} gal > {high:,.0f}: cap towers at 20 MW, sell and store first")
    if unmet_hours > UNMET_HOURS_HIGH:
        p["storage_reserve"] = min(0.6, p.get("storage_reserve", 0.2) + 0.1)
        why.append(f"{unmet_hours} h unmet demand: raise storage reserve to {p['storage_reserve']:.1f}")
    if pit_empty_hours > PIT_EMPTY_HOURS_HIGH:
        p["storage_reserve"] = min(0.6, p.get("storage_reserve", 0.2) + 0.1)
        why.append(f"pit empty {pit_empty_hours} h: charge earlier")
    return p, "; ".join(why) or "no change"


def plan_year_alpine(daily: dict, base: dict | None = None, water_high: float | None = None,
                     days: int = 365) -> dict:
    """Sabina's decide() on every day of the baseline answers. Returns {days, policy_by_day, notes, decisions}."""
    ans = daily["answers"]
    water, unmet, empty = (ans[k]["by_day"] for k in ("water_gal", "unmet_hours", "empty_hours"))
    policy = dict(base or _thermal().DEFAULT_POLICY)
    log = [{"day": 0, "date": day_date(0), "questions": [], "policy": dict(policy),
            "reason": "day 0: default policy (no previous day to look at)", "changed": False}]
    flat = []      # out/decisions.json shape the web page reads: {day, question, answer, decision, changed}
    by_day: dict[int, dict] = {}
    notes: dict[int, str] = {}
    for d in range(1, days):
        y = d - 1
        w, u, e = water.get(y, 0.0), int(unmet.get(y, 0)), int(empty.get(y, 0))
        new, reason = decide(w, u, e, policy, water_high=water_high)
        changed = new != policy
        questions = [{"question": ans[k]["question"], "query": ans[k]["query"], "answer_day": y,
                      "answer": v, "source": daily["source"]}
                     for k, v in (("water_gal", w), ("unmet_hours", u), ("empty_hours", e))]
        log.append({"day": d, "date": day_date(d), "questions": questions, "policy": dict(new),
                    "reason": reason, "changed": changed})
        flat.append({"day": d, "question": " / ".join(q["question"] for q in questions),
                     "answer": f"{w:,.0f} gal, {u} h unmet, {e} h pit empty",
                     "decision": reason, "changed": changed})
        if changed:
            by_day[d] = dict(new)
            notes[d] = reason[:256]
        policy = new
    return {"days": log, "policy_by_day": by_day, "notes": notes, "decisions": flat}


def plan_year(daily: dict, water_pct: float = 75.0, water_gal: float | None = None,
              base: Policy | None = None, days: int = 365) -> dict:
    """Apply the rules to every day. Returns {threshold, days:[...], policies, notes}."""
    ans = daily["answers"]
    water = ans["water_gal"]["by_day"]
    unmet = ans["unmet_hours"]["by_day"]
    empty = ans["empty_hours"]["by_day"]
    threshold = float(water_gal) if water_gal is not None else water_threshold(water, water_pct)
    top_label = (f"> {_fmt(threshold)} gal limit" if water_gal is not None
                 else f"top {100 - water_pct:.0f}%")
    floor_min = base.discharge_floor if base else 0.05
    policy = replace(base) if base else Policy()

    log = [{
        "day": 0, "date": day_date(0), "questions": [], "policy": asdict(policy),
        "reason": "day 0: default policy (no previous day to look at)", "changed": False,
    }]
    policies = {0: replace(policy)}
    notes: dict[int, str] = {}
    calm = 0
    for d in range(1, days):
        y = d - 1
        w, u, e = water.get(y, 0.0), unmet.get(y, 0.0), empty.get(y, 0.0)
        new, reason, calm = decide_basic(policy, w, u, e, threshold, calm, top_label, floor_min)
        changed = new != policy
        questions = [
            {"question": ans[k]["question"], "query": ans[k]["query"], "answer_day": y,
             "answer": v, "source": daily["source"]}
            for k, v in (("water_gal", w), ("unmet_hours", u), ("empty_hours", e))
        ]
        log.append({"day": d, "date": day_date(d), "questions": questions,
                    "policy": asdict(new), "reason": reason, "changed": changed})
        policies[d] = replace(new)
        if changed:
            notes[d] = reason[:256]
        policy = new
    return {"threshold": threshold, "days": log, "policies": policies, "notes": notes}


# ----------------------------------------------------------------------------- totals

TOTAL_KEYS = ["water_gal", "power_kwh", "cost_usd", "unmet_demand_kw"]


def hourly_totals(hourly: list[dict]) -> dict[str, float]:
    return {k: float(sum(float(r.get(k, 0) or 0) for r in hourly)) for k in TOTAL_KEYS}


def compare_totals(base: dict, agent: dict) -> dict:
    out = {}
    for k in TOTAL_KEYS:
        b, a = base.get(k, 0.0), agent.get(k, 0.0)
        out[k] = {"baseline": b, "agent": a, "delta": a - b,
                  "delta_pct": (100.0 * (a - b) / b) if b else None}
    return out


def print_comparison(cmp: dict) -> None:
    labels = {"water_gal": "water (gal)", "power_kwh": "power (kWh)",
              "cost_usd": "cost (USD)", "unmet_demand_kw": "unmet (kWh)"}
    print(f"{'':22}{'baseline':>18}{'agent':>18}{'delta':>10}")
    for k, v in cmp.items():
        pct = f"{v['delta_pct']:+.1f}%" if v["delta_pct"] is not None else "n/a"
        print(f"{labels[k]:22}{_fmt(v['baseline']):>18}{_fmt(v['agent']):>18}{pct:>10}")


# --------------------------------------------------------------------------- pipeline

def run_daily_agent(baseline_run_id: str, offline: bool = False, push: bool = True,
                    water_pct: float = 75.0, water_gal: float | None = None,
                    resimulate: bool = True, agent_run_id: str | None = None, sim: str | None = None) -> dict:
    """sim="alpine" (thermal.py + Sabina's decide) or "basic" (thermal_basic.py + decide_basic).
    Default: the baseline run's own sim from index.json, else alpine."""
    agent_run_id = agent_run_id or f"agent-{uuid.uuid4().hex[:4]}"
    sim = sim or run_sim(baseline_run_id)
    if offline:
        daily = fetch_daily_answers_offline(baseline_run_id)
    else:
        if not _keewano().sql_available():
            raise SystemExit("Keewano SQL is not configured (KEEWANO_EMAIL / KEEWANO_PAT / "
                             "KEEWANO_PROJECT_ID). Re-run with --offline to use the local CSV.")
        daily = fetch_daily_answers_keewano(baseline_run_id)

    if sim == "alpine":
        return _run_daily_agent_alpine(baseline_run_id, daily, agent_run_id, push, water_pct, water_gal,
                                       resimulate)
    plan = plan_year(daily, water_pct=water_pct, water_gal=water_gal)
    n_changed = sum(1 for d in plan["days"] if d["changed"])
    doc = {
        "agent_run_id": agent_run_id,
        "baseline_run_id": baseline_run_id,
        "source": daily["source"],
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "rules": {"water_threshold_gal": plan["threshold"], "water_pct": water_pct,
                  "water_gal_override": water_gal, "dry_boost_step": DRY_BOOST_STEP,
                  "dry_boost_decay": DRY_BOOST_DECAY, "dry_boost_max": DRY_BOOST_MAX,
                  "empty_hours_limit": EMPTY_HOURS_LIMIT,
                  "precharge_calm_days": PRECHARGE_CALM_DAYS},
        "days_changed": n_changed,
        "days": plan["days"],
    }
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"decisions_{agent_run_id}.json"
    path.write_text(json.dumps(doc, indent=1))
    print(f"[{daily['source']}] {n_changed} policy changes over 364 days → {path}")

    if not resimulate:
        return doc

    th = _thermal_basic()
    run = th.simulate(scenario="agent", run_id=agent_run_id, policies=plan["policies"],
                      policy_notes=plan["notes"])
    saved = th.save_run(run)
    print(f"agent run saved: {saved}")
    if push:
        res = _keewano().send_run(run)
        doc["push"] = res
        print(f"pushed to Keewano: {res}")
    else:
        print("--no-push: not sent to Keewano")

    try:
        base_hourly = load_hourly_csv(baseline_run_id)
        base_tot = hourly_totals(base_hourly)
    except FileNotFoundError:
        base_tot = next((r.get("totals", {}) for r in load_index()
                         if r.get("run_id") == baseline_run_id), {})
    cmp = compare_totals(base_tot, hourly_totals(run.hourly))
    doc["comparison"] = cmp
    path.write_text(json.dumps(doc, indent=1))
    print_comparison(cmp)
    return doc


def _run_daily_agent_alpine(baseline_run_id: str, daily: dict, agent_run_id: str, push: bool,
                            water_pct: float, water_gal: float | None, resimulate: bool) -> dict:
    """Alpine path: Keewano answers about the baseline -> decide() per day -> thermal.run(policy_by_day)."""
    water_by_day = daily["answers"]["water_gal"]["by_day"]
    if water_gal is not None:
        high = float(water_gal)
    elif water_pct is not None and water_by_day and max(water_by_day.values()) < WATER_GAL_PER_DAY_HIGH:
        high = water_threshold(water_by_day, water_pct)   # fixed threshold never trips on this year
    else:
        high = None                                        # Sabina's WATER_GAL_PER_DAY_HIGH
    plan = plan_year_alpine(daily, water_high=high)
    n_changed = sum(1 for d in plan["days"] if d["changed"])
    doc = {"agent_run_id": agent_run_id, "baseline_run_id": baseline_run_id, "sim": "alpine",
           "source": daily["source"],
           "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "rules": {"water_gal_per_day_high": high if high is not None else WATER_GAL_PER_DAY_HIGH,
                     "unmet_hours_high": UNMET_HOURS_HIGH, "pit_empty_hours_high": PIT_EMPTY_HOURS_HIGH},
           "days_changed": n_changed, "days": plan["days"]}
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"decisions_{agent_run_id}.json"
    path.write_text(json.dumps(doc, indent=1, default=str))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "decisions.json").write_text(json.dumps(plan["decisions"], indent=1, default=str))
    print(f"[{daily['source']}] {n_changed} policy changes over 364 days → {path}")
    if not resimulate:
        return doc
    res = _thermal().run(scenario="agent", push=push, policy_by_day=plan["policy_by_day"],
                         policy_notes=plan["notes"], run_id=agent_run_id, save=True)
    print(f"agent run saved: outputs/runs/{agent_run_id}.csv" + (f", {res['n_sent']} events sent" if push else
                                                                 " (--no-push: not sent to Keewano)"))
    try:
        base_tot = hourly_totals(load_hourly_csv(baseline_run_id))
    except FileNotFoundError:
        base_tot = {}
    agent_tot = hourly_totals(res["kw_run"].hourly)
    for t in (base_tot, agent_tot):   # Alpine hourly rows hold cost in cents (Keewano units)
        if "cost_usd" in t:
            t["cost_usd"] /= 100.0
    cmp = compare_totals(base_tot, agent_tot)
    doc["comparison"], doc["summary"] = cmp, res["summary"]
    path.write_text(json.dumps(doc, indent=1, default=str))
    print_comparison(cmp)
    return doc


def daily_loop(run_id: str) -> dict:
    """Scaffold name for the daily agent (Keewano answers -> policy -> agent run)."""
    return run_daily_agent(run_id)


def report(run_id: str | None = None) -> dict:
    """The three metrics live from Keewano, in the web page's shape:
    {run_id, cached: False, computed_at, metrics: [{name, value, unit, query}]}."""
    run_id = run_id or latest_run("normal")
    if not run_id:
        raise RuntimeError("no normal run in outputs/runs/index.json")
    r = _keewano().run_report(run_id)
    units = {"pue": ("PUE", "facility kWh / IT kWh"), "wue": ("WUE", "L / IT kWh"),
             "erf": ("ERF", "heat reused / energy in")}
    return {"run_id": run_id, "cached": False, "source": "keewano",
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "metrics": [{"name": units[k][0], "value": r[k]["value"], "unit": units[k][1],
                         "query": r[k]["query"]} for k in ("pue", "wue", "erf")]}


# ------------------------------------------------------------------------------- /ask

def _site_facts() -> str:
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config" / "site.yaml").read_text())
        s, dc, c, hn, st, pr = (cfg["site"], cfg["data_center"], cfg["cooling"], cfg["heat_network"],
                                cfg["storage"], cfg["prices"])
        return (f"- Site: {s['name']}, {dc['it_load_mw']:.0f} MW IT, liquid cooled (capture "
                f"{dc['capture_fraction']:.0%}), supply {dc['supply_temp_c']:.0f} C.\n"
                f"- Cooling: chillers COP {c['chiller_cop']} on towers / {c['chiller_cop_dry']} on dry "
                f"condensers; dry coolers {c['dry_cooler_capacity_mw']:.0f} MW; towers use "
                f"{c['water_gal_per_kwh']} gal per kWh of cooling electricity.\n"
                f"- Heat network: {hn['supply_temp_c']:.0f} C supply, {hn['route_km']} km route at "
                f"{hn['pipe_loss_per_km']:.0%}/km loss, heat sold at ${hn['heat_price_usd_per_mwh']:.0f}/MWh.\n"
                f"- Storage pit: {st['capacity_mwh']:.0f} MWh, charge/discharge "
                f"{st['max_charge_mw']:.0f}/{st['max_discharge_mw']:.0f} MW, min SOC {st['min_soc'] * 100:.0f}%.\n"
                f"- Prices: electricity ${pr['electricity_usd_per_mwh']:.0f}/MWh, water "
                f"${pr['water_usd_per_kgal']}/kgal.\n"
                f"- Scenario windows (hour of year): {json.dumps(cfg.get('scenarios', {}))}")
    except Exception:  # noqa: BLE001 - facts are a nice-to-have
        return "- Site: 100 MW liquid-cooled data center, Lansing NY."


def build_system_prompt() -> str:
    catalog = "\n".join(f"- {name} ({dtype}): {meaning}" for _, name, dtype, meaning in schema.EVENTS)
    try:
        examples = QUESTIONS_PATH.read_text()
    except FileNotFoundError:
        examples = "(questions.md missing)"
    return f"""You answer questions about Thermal Layer, a simulation of a 100 MW liquid-cooled \
AI data center at Lansing, NY, on the east shore of Cayuga Lake. Its waste heat is either sold to a \
district heat network (directly or through heat pumps), stored in a seasonal pit, or rejected \
through dry coolers (electricity only) or cooling towers (electricity + water). A typical weather \
year is mapped onto calendar 2025 and simulated hourly (8,760 hours). Every hour is streamed into \
Keewano, an analytics database, and you answer only from Keewano queries.

## Site facts
{_site_facts()}

## Event catalog (every event name is a column of the single `events` table)
Keewano has no floats: values are unsigned integers in the unit shown. price_usd_cent columns are \
money; Keewano renders them as a double (check whether the result looks like dollars or cents).
{catalog}

## How the data is shaped
- One Keewano entity (user_id) per run. Each run starts with run_id and scenario events, so \
`WHERE run_id = '<id>'` or `WHERE scenario = '<name>'` selects whole runs. If several runs share a \
scenario, GROUP BY run_id. Scenarios: normal (baseline), heat_wave, cold_snap, server_outage, \
agent (the daily policy agent's run).
- Columns are step functions: a value holds until the next event of that name. hour (0..8759), \
day (0..364) and month (1..12) are context that scope the measures after them, so \
`WHERE day = 41` or `WHERE month = 1` or `WHERE hour BETWEEN 4680 AND 4799` selects that stretch. \
hour = day*24 + hour_of_day; day 0 = 2025-01-01.
- Each measure is emitted once per hour, so sum(col) over a stretch adds the hourly values. \
count(col) counts hours (occurrences). To count hours matching a condition use \
`count(col) FILTER (WHERE col > 0)`.
- policy_change events (strings) appear in the agent run at hour 0 of a day when the agent changed \
policy; the text is the agent's reason. Runs from the main (Alpine) sim emit policy_prefer_dry, \
policy_storage_reserve_pct and policy_tower_cap_mw every hour; runs from the basic sim emit \
policy_dry_boost_pct and policy_discharge_floor_pct instead.
- In Alpine runs cost_usd is NET cost (electricity + water - heat sales; electricity_cost_usd, \
water_cost_usd and heat_revenue_usd are the parts), reject_dry_kw/reject_tower_kw include the chiller \
condenser heat, and net CO2 = co2_kg - co2_avoided_kg.

## Units and conversions
- *_kw columns are average kW over one hour, so sum(x_kw) = kWh. 1 MWh = 1000 kWh.
- *_kwh columns are already kWh per hour; sum() is kWh.
- water_gal: gallons; litres = gal * 3.785.
- ambient_k: kelvin; Celsius = value - 273.
- co2_kg: kg; tonnes = kg / 1000.
- cost_usd and heat_revenue_usd are price_usd_cent (see note above).

## Metrics
- PUE = sum(power_kwh) / sum(it_kwh)
- WUE (L/kWh IT) = sum(water_gal) * 3.785 / sum(it_kwh)
- ERF (energy reuse factor) = sum(heat_exported_kw) / sum(power_kwh)
- Heat reuse share = sum(heat_exported_kw) / sum(heat_available_kw)
- Compute ratios in SELECT (aggregates may be combined with arithmetic), e.g. \
`1.0 * sum(power_kwh) / sum(it_kwh) AS pue`.

## Keewano SQL dialect gotchas
- Always `FROM events`. No JOIN, no CTE (WITH), no window functions (OVER), no UNION. \
Use `FROM (SELECT ...) t` subqueries for two-stage aggregation.
- WHERE leaves compare one column against constants (=, <>, IN, BETWEEN, LIKE, IS NULL). \
No arithmetic on columns in WHERE: put it on the constant side (`ambient_k > 303` for > 30 C).
- FILTER (WHERE ...) samples at occurrences; supported for count/sum/avg/min/max.
- To find the hour with the max of something: `SELECT hour, sum(x) AS x FROM events WHERE ... \
GROUP BY hour ORDER BY x DESC LIMIT 1`.
- No string building (no concat/substring). round(x) takes no scale argument.
- Columns never emitted in a stretch are NULL; add `AND day IS NOT NULL` when grouping by day.
- Max 16 aggregates and 16 distinct columns per query.

## Example questions with the SQL that answers them
{examples}

## How to answer
- Always answer from a query you ran (prefer the run_sql tool). If a query errors, read the error, \
fix the SQL, and retry.
- Give the number with units (convert to friendly units: gal and L, MWh, tonnes, dollars, C) in one \
or two sentences, and say which run (run_id and scenario) it is about.
- If the user does not name a run, use the latest normal run for single-run questions, and say so.
- Do not invent numbers. If the data cannot answer the question, say what is missing."""


def _known_runs_text(run_id: str | None) -> str:
    runs = load_index()
    lines = [f"- {r.get('run_id')} (scenario {r.get('scenario')}, created {r.get('created')})"
             for r in runs[-30:]]
    txt = "## Known runs (outputs/runs/index.json)\n" + ("\n".join(lines) if lines else "- none listed")
    if os.environ.get("KEEWANO_PROJECT_ID"):
        txt += f"\n\nKeewano projectId for MCP tools: {os.environ['KEEWANO_PROJECT_ID']}"
    if run_id:
        txt += f"\n\nThe user is looking at run {run_id}; default to it."
    else:
        default = latest_run("normal")
        if default:
            txt += f"\n\nDefault run if none is named: {default} (latest normal run)."
    return txt


RUN_SQL_TOOL = {
    "name": "run_sql",
    "description": ("Run one read-only SQL query against the Keewano `events` table (Keewano SQL "
                    "dialect, see system prompt) and get the result rows as JSON. Prefer this tool "
                    "for numeric answers."),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "A single SELECT ... FROM events query."}},
        "required": ["query"],
        "additionalProperties": False,
    },
}


def mcp_tools_to_anthropic(tools: list[dict], skip: set[str] = frozenset()) -> list[dict]:
    out = []
    for t in tools:
        name = t.get("name")
        if not name or name in skip:
            continue
        schema_ = t.get("input_schema") or t.get("inputSchema") or {"type": "object", "properties": {}}
        out.append({"name": name, "description": (t.get("description") or "")[:4000],
                    "input_schema": schema_})
    return out


def _try_json(text: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _text_of(content) -> str:
    parts = []
    for b in content or []:
        if getattr(b, "type", None) == "text":
            parts.append(b.text)
    return "\n".join(parts).strip()


async def ask_async(question: str, run_id: str | None = None, client=None, mcp=None,
                    max_iters: int = ASK_MAX_ITERS, model: str = ASK_MODEL) -> dict:
    """Claude tool-use loop over Keewano. Returns {answer, query, rows, tool_calls}."""
    kw = _keewano()
    if client is None:
        import anthropic
        client = anthropic.AsyncAnthropic()

    tool_calls: list[dict] = []
    last_query: str | None = None
    last_rows: list | None = None
    warnings: list[str] = []

    # Manual stack: the MCP streamable-HTTP client can raise an ExceptionGroup ("unhandled errors in a
    # TaskGroup") when its streams close, even after the answer is in hand. Closing in a guarded finally
    # keeps the answer instead of turning it into a 503.
    stack = contextlib.AsyncExitStack()
    await stack.__aenter__()
    try:
        mcp_tools: list[dict] = []
        if mcp is None and os.getenv("KEEWANO_PAT"):
            try:
                mcp = await stack.enter_async_context(kw.KeewanoMCP())
            except Exception as e:  # noqa: BLE001 - fall back to run_sql only
                warnings.append(f"MCP unavailable: {e}")
                mcp = None
        if mcp is not None:
            try:
                mcp_tools = await mcp.list_tools()
            except Exception as e:  # noqa: BLE001
                warnings.append(f"MCP list_tools failed: {e}")
                mcp_tools = []

        sql_ok = False
        try:
            sql_ok = bool(kw.sql_available())
        except Exception as e:  # noqa: BLE001
            warnings.append(f"sql_available failed: {e}")
        tools = ([RUN_SQL_TOOL] if sql_ok else []) + mcp_tools_to_anthropic(
            mcp_tools, skip={"run_sql"})
        mcp_names = {t["name"] for t in tools} - {"run_sql"}
        if not tools:
            return {"answer": "Keewano is not configured (no SQL access and no MCP tools), so I "
                              "can't answer from data yet.",
                    "query": None, "rows": None, "tool_calls": [], "warnings": warnings}

        system = [
            {"type": "text", "text": build_system_prompt(), "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": _known_runs_text(run_id)},
        ]
        messages: list[dict] = [{"role": "user", "content": question}]
        answer = ""
        for _ in range(max_iters):
            resp = await client.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if resp.stop_reason == "refusal":
                answer = "The model declined to answer this question."
                break
            messages.append({"role": "assistant", "content": resp.content})
            text = _text_of(resp.content)
            if text:
                answer = text
            if resp.stop_reason != "tool_use":
                break

            results = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                args = block.input if isinstance(block.input, dict) else (_try_json(block.input) or {})
                call = {"name": block.name, "input": args}
                try:
                    if block.name == "run_sql":
                        query = args.get("query", "")
                        rows = await asyncio.to_thread(kw.sql, query)
                        last_query, last_rows = query, rows
                        payload = json.dumps(rows[:200], default=str)
                        if len(rows) > 200:
                            payload += f"\n({len(rows)} rows total, first 200 shown)"
                        call.update(ok=True, row_count=len(rows))
                    elif block.name in mcp_names and mcp is not None:
                        payload = await mcp.call_tool(block.name, args)
                        q = args.get("query") or args.get("sql") or args.get("sqlQuery")
                        if isinstance(q, str) and q.strip():
                            parsed = _try_json(payload)
                            if parsed is None and isinstance(payload, str) and payload.startswith("SQL result"):
                                import csv, io
                                body = payload.split("\n", 1)[1] if "\n" in payload else ""
                                parsed = list(csv.DictReader(io.StringIO(body)))
                            last_query = q
                            if isinstance(parsed, dict) and isinstance(parsed.get("rows"), list):
                                cols = parsed.get("columns") or []
                                names = [c.get("name", str(i)) if isinstance(c, dict) else str(c)
                                         for i, c in enumerate(cols)]
                                parsed = [dict(zip(names, r)) if isinstance(r, list) and names else r
                                          for r in parsed["rows"]]
                            last_rows = parsed if isinstance(parsed, list) else None
                        call.update(ok=True)
                        payload = payload if isinstance(payload, str) else json.dumps(payload, default=str)
                    else:
                        raise ValueError(f"unknown tool {block.name}")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": payload[:50000]})
                except Exception as e:  # noqa: BLE001 - report errors back to Claude
                    call.update(ok=False, error=str(e)[:500])
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"Error: {e}", "is_error": True})
                tool_calls.append(call)
            messages.append({"role": "user", "content": results})
        else:
            if not answer:
                answer = "I ran out of steps before reaching an answer."
    finally:
        try:
            await stack.aclose()
        except BaseException as e:  # noqa: BLE001 - see note above
            warnings.append(f"MCP teardown: {str(e)[:200]}")

    out = {"answer": answer, "query": last_query, "rows": last_rows, "tool_calls": tool_calls}
    if warnings:
        out["warnings"] = warnings
    return out


def ask(question: str, run_id: str | None = None, **kw) -> dict:
    """Sync wrapper (call from a thread, not from inside a running event loop)."""
    return asyncio.run(ask_async(question, run_id=run_id, **kw))


# -------------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(ImportError):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Thermal Layer daily policy agent")
    ap.add_argument("--run", help="baseline run id (default: latest normal run in index.json)")
    ap.add_argument("--offline", action="store_true",
                    help="compute the 3 daily answers from outputs/runs/<run>.csv instead of Keewano")
    ap.add_argument("--no-push", action="store_true", help="don't send the agent run to Keewano")
    ap.add_argument("--no-sim", action="store_true", help="only write the decisions log")
    ap.add_argument("--water-pct", type=float, default=75.0,
                    help="percentile of baseline daily water that counts as high (default 75)")
    ap.add_argument("--water-gal", type=float, default=None, help="absolute daily water threshold")
    ap.add_argument("--ask", help="answer one question via /ask instead of running the daily agent")
    ap.add_argument("--sim", choices=["alpine", "basic"], default=None,
                    help="which sim re-runs the year (default: the baseline run's own sim)")
    args = ap.parse_args(argv)

    if args.ask:
        print(json.dumps(ask(args.ask, run_id=args.run), indent=1, default=str))
        return 0

    run_id = args.run or latest_run("normal", sim=args.sim or "alpine") or latest_run("normal")
    if not run_id:
        print("no baseline run given and none in outputs/runs/index.json", file=sys.stderr)
        return 2
    run_daily_agent(run_id, offline=args.offline, push=not args.no_push,
                    water_pct=args.water_pct, water_gal=args.water_gal,
                    resimulate=not args.no_sim, sim=args.sim)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
