"""Hourly simulation and rules policy for one data center. Emits Keewano events.

    run(scenario="normal", push=True, policy=None) -> {"run_id", "n_events", "summary", "events"}

Each hour, in this order (all heat figures are SOURCE heat captured from the servers, so the
energy balance  available == exported + stored + rejected  holds exactly):
  1. available = captured heat this hour (data/supply.csv, P50) x scenario supply_x
  2. sell     = source heat the network can take: demand / (1 - pipe loss) x (cop-1)/cop, capped by available
  3. discharge the pit when demand is still unmet (stored_mw negative), down to the policy reserve
  4. store    = surplus into the pit, up to max_charge and free capacity
  5. reject   = the rest of the CAPTURED heat: dry coolers at 55 C up to their capacity (fan power only),
                towers above it. The UNCAPTURED quarter of IT heat always goes through chillers; the policy
                picks what cools the chiller condensers: towers (less fan power, uses water) or dry
                condensers (more fan power, no water, and they derate above 25 C ambient so hot hours
                spill back to towers). That is the agent's real water-vs-cost lever (prefer_dry).
  6. power    = IT + chillers + condenser/dry-cooler/tower fans + network heat pump + pumps
  7. cost and CO2 from config prices; CO2 is net of the boiler gas the delivered heat replaces

Policy knobs the daily agent moves (agent.py): prefer_dry (bool), storage_reserve (0-1, SOC floor
for discharge), tower_cap_mw (float). Defaults in DEFAULT_POLICY.

COP: the supply model's own hourly COP (cop_p50, ~7) is used when present; otherwise cop() below.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

CFG = yaml.safe_load(Path("config/site.yaml").read_text())
DEFAULT_POLICY = {"prefer_dry": False, "storage_reserve": 0.2, "tower_cap_mw": 1e9}
DRY_DERATE_START_C, DRY_DERATE_END_C = 25.0, 35.0   # dry condenser capacity fades to zero across this ambient range; assumption - verify

EVENT_NAMES = [
    "heat_available_mw", "heat_exported_mw", "heat_stored_mw", "heat_rejected_mw", "heat_tower_mw", "heat_dry_mw",
    "water_gal", "power_kwh", "it_kwh", "cost_usd", "co2_t", "decision", "storage_soc",
]
BOILER_EFF = 0.85  # gas boiler efficiency the delivered heat replaces; assumption - verify


def site_cfg(site: str = "lansing") -> dict:
    """Lansing: the module-level CFG (the server's /compare and /sensitivity mutate it). Other sites:
    sites/<id>/site.yaml (sites.py)."""
    if site in (None, "lansing"):
        return CFG
    import sites
    return sites.load_cfg(site)


def load_inputs(site: str = "lansing") -> pd.DataFrame:
    if site in (None, "lansing"):
        sp, dp = "data/supply.csv", "data/demand.csv"
    else:
        import sites
        p = sites.paths(sites.check_id(site))
        sp, dp = p["supply"], p["demand"]
    s = pd.read_csv(sp, parse_dates=["timestamp"])
    d = pd.read_csv(dp, parse_dates=["timestamp"])
    df = s.merge(d, on="timestamp")
    df["hour"] = range(len(df))
    df["day"] = df["hour"] // 24
    df["month"] = df["timestamp"].dt.month
    return df


def cop(t_source_c: float, t_sink_c: float, carnot_fraction: float) -> float:
    """Heating COP = carnot_fraction x T_sink / (T_sink - T_source), temperatures in K."""
    ts, tk = t_source_c + 273.15, t_sink_c + 273.15
    return max(1.0, carnot_fraction * tk / max(tk - ts, 1.0))


def _scenario_window(scenario: str, cfg: dict | None = None) -> tuple[int, int, dict]:
    sc = (cfg or CFG)["scenarios"].get(scenario) or {}
    return sc.get("start_hour", -1), sc.get("hours", 0), sc


def run(scenario: str = "normal", push: bool = True, policy: dict | None = None,
        policy_by_day: dict[int, dict] | None = None, policy_notes: dict[int, str] | None = None,
        run_id: str | None = None, save: bool | None = None, site: str = "lansing",
        cfg: dict | None = None) -> dict:
    """Simulate a typical year. policy_by_day lets agent.py change the policy from a given day on;
    policy_notes (day -> reason) become policy_change events in Keewano.

    What gets written:
      out/run_<id>.json + out/events_<id>.csv   (the web page's runs)        unless save=False
      outputs/runs/<id>.csv + index.json        (runs that exist in Keewano) when pushed, or save=True
    save=None (default) means "same as push" for the Keewano index. What-if calls (/compare,
    /sensitivity, tests) pass save=False so nothing is written and nothing shows up as a run.
    site: "lansing" (default, config/site.yaml + data/) or a sites.py id (sites/<id>/).
    cfg: a what-if copy of the site's config (/sensitivity on a non-Lansing site); default the site's own."""
    site = site or "lansing"
    cfg = cfg or site_cfg(site)
    df = load_inputs(site)
    c, hn, st, pr, em = cfg["cooling"], cfg["heat_network"], cfg["storage"], cfg["prices"], cfg["emissions"]
    dc = cfg["data_center"]
    start, nh, sc = _scenario_window(scenario, cfg)
    pol = dict(DEFAULT_POLICY, **(policy or {}))
    policy_by_day = policy_by_day or {}

    deliver_frac = 1.0 - hn["pipe_loss_per_km"] * hn["route_km"]
    cap = st["capacity_mwh"]
    soc = st["initial_soc"]
    run_id = run_id or f"{scenario}-{datetime.now(timezone.utc):%m%d%H%M}-{uuid.uuid4().hex[:4]}"
    events: list[dict] = []
    kw_rows: list[dict] = []   # the same hour in Keewano units (schema.py), for ingest and the agent
    tot = {k: 0.0 for k in ("avail", "export", "store_in", "store_out", "reject", "dry", "tower", "water",
                             "power", "it", "cost", "co2", "delivered", "hours_tower", "hours_pit_empty",
                             "hours_unmet")}

    for r in df.itertuples(index=False):
        h, day = int(r.hour), int(r.day)
        if day in policy_by_day:
            pol = dict(pol, **policy_by_day[day])
        in_sc = start <= h < start + nh
        supply_x = sc.get("supply_x", 1.0) if in_sc else 1.0
        demand_x = sc.get("demand_x", 1.0) if in_sc else 1.0

        it_mw = float(r.it_mw_p50)
        avail = float(r.source_heat_mw_p50) * supply_x
        demand = float(r.demand_mw_p50) * demand_x
        k = float(getattr(r, "cop_p50", 0) or 0) or cop(dc["supply_temp_c"], hn["supply_temp_c"],
                                                           hn["heat_pump_carnot_fraction"])
        src_per_delivered = (k - 1.0) / k / deliver_frac   # source MW needed per MW delivered at buildings
        demand_src = demand * src_per_delivered

        # 2. sell
        export = min(avail, demand_src)
        unmet_src = demand_src - export
        # 3. discharge
        floor = max(st["min_soc"], pol["storage_reserve"])
        discharge = min(unmet_src, st["max_discharge_mw"], max(0.0, (soc - floor) * cap)) if unmet_src > 0 else 0.0
        unmet_src -= discharge
        # 4. store
        surplus = avail - export
        store = min(surplus, st["max_charge_mw"], max(0.0, (1.0 - soc) * cap))
        # 5. reject the captured surplus: dry coolers at 55 C first, towers above their capacity
        reject = surplus - store
        dry = min(reject, c["dry_cooler_capacity_mw"])
        tower = reject - dry
        soc = soc * (1.0 - st["loss_per_hour"]) + (store - discharge) / cap

        # 5b. the uncaptured heat goes through chillers; the policy picks the condenser cooling
        uncaptured = max(0.0, it_mw - float(r.source_heat_mw_p50))
        amb = float(r.outdoor_c) + (sc.get("ambient_plus_c", 0.0) if in_sc else 0.0)
        derate = min(1.0, max(0.0, (DRY_DERATE_END_C - amb) / (DRY_DERATE_END_C - DRY_DERATE_START_C)))
        cond_dry = uncaptured * derate if pol["prefer_dry"] else 0.0
        cond_tower = min(uncaptured - cond_dry, pol["tower_cap_mw"])
        cond_dry += uncaptured - cond_dry - cond_tower   # over the tower cap, dry condensers run above rating
        tower_total = tower + cond_tower

        # 6. power (kWh this hour = MW x 1000)
        delivered = (export + discharge) / src_per_delivered
        hp_elec = (export + discharge) / (k - 1.0)
        chiller_kwh = cond_tower * 1000.0 / c["chiller_cop"] + cond_dry * 1000.0 / c["chiller_cop_dry"]
        fan_kwh = (dry + cond_dry) * c["dry_cooler_kw_per_mw_th"] + tower_total * c["tower_kw_per_mw_th"]
        pump_kwh = (export + discharge) * hn["pump_kw_per_mw_th"]
        it_kwh = it_mw * 1000.0
        power_kwh = it_kwh + chiller_kwh + fan_kwh + hp_elec * 1000.0 + pump_kwh
        # water: evaporative towers, 2 gal per kWh of the electricity whose heat they reject (chiller share + fans)
        tower_elec_kwh = cond_tower * 1000.0 / c["chiller_cop"] + tower_total * c["tower_kw_per_mw_th"]
        water_gal = c["water_gal_per_kwh"] * tower_elec_kwh

        # 7. money and carbon
        cost = (power_kwh / 1000.0) * pr["electricity_usd_per_mwh"] + water_gal / 1000.0 * pr["water_usd_per_kgal"] \
            - delivered * hn["heat_price_usd_per_mwh"]
        co2 = (power_kwh / 1000.0) * em["grid_t_per_mwh"] - delivered / BOILER_EFF * em["gas_boiler_t_per_mwh"]

        paths = {"sell": export, "store": store, "reject_dry": dry + cond_dry, "reject_tower": tower_total}
        decision = max(paths, key=paths.get) if max(paths.values()) > 0 else "idle"

        tags = {"hour": h, "day": day, "month": int(r.month), "scenario": scenario if in_sc else "normal",
                "run_id": run_id}
        vals = {
            "heat_available_mw": avail, "heat_exported_mw": export, "heat_stored_mw": store - discharge,
            "heat_rejected_mw": reject, "heat_tower_mw": tower_total, "heat_dry_mw": dry + cond_dry, "water_gal": water_gal, "power_kwh": power_kwh, "it_kwh": it_kwh,
            "cost_usd": cost, "co2_t": co2, "decision": decision, "storage_soc": soc,
        }
        events.extend({"name": n, "value": (round(v, 4) if isinstance(v, float) else v), "tags": tags}
                      for n, v in vals.items())
        elec_usd = (power_kwh / 1000.0) * pr["electricity_usd_per_mwh"]
        water_usd = water_gal / 1000.0 * pr["water_usd_per_kgal"]
        kw_rows.append({
            "hour": h, "day": day, "month": int(r.month), "ambient_k": amb + 273.15, "decision": decision,
            "policy_prefer_dry": int(bool(pol["prefer_dry"])),
            "policy_storage_reserve_pct": pol["storage_reserve"] * 100,
            "policy_tower_cap_mw": min(pol["tower_cap_mw"], 9999),
            "heat_available_kw": avail * 1000, "demand_kw": demand * 1000, "heat_exported_kw": export * 1000,
            "heat_stored_kw": store * 1000, "heat_discharged_kw": discharge * 1000,
            "heat_rejected_kw": reject * 1000, "reject_dry_kw": (dry + cond_dry) * 1000,
            "reject_tower_kw": tower_total * 1000, "unmet_demand_kw": unmet_src / src_per_delivered * 1000,
            "water_gal": water_gal, "it_kwh": it_kwh, "cooling_kwh": chiller_kwh + fan_kwh,
            "heat_pump_kwh": hp_elec * 1000.0, "pump_kwh": pump_kwh, "power_kwh": power_kwh,
            "cost_usd": cost * 100, "heat_revenue_usd": delivered * hn["heat_price_usd_per_mwh"] * 100,
            "co2_kg": (power_kwh / 1000.0) * em["grid_t_per_mwh"] * 1000,
            "co2_avoided_kg": delivered / BOILER_EFF * em["gas_boiler_t_per_mwh"] * 1000,
            "storage_soc_pct": soc * 100, "heat_delivered_kw": delivered * 1000,
            "electricity_cost_usd": elec_usd * 100, "water_cost_usd": water_usd * 100,
        })

        tot["avail"] += avail; tot["export"] += export; tot["store_in"] += store; tot["store_out"] += discharge
        tot["reject"] += reject; tot["dry"] += dry + cond_dry; tot["tower"] += tower_total; tot["water"] += water_gal
        tot["power"] += power_kwh; tot["it"] += it_kwh; tot["cost"] += cost; tot["co2"] += co2
        tot["delivered"] += delivered
        tot["hours_tower"] += tower_total > 0; tot["hours_pit_empty"] += soc <= st["min_soc"] + 1e-9
        tot["hours_unmet"] += unmet_src > 1e-6

    summary = {
        "pue": tot["power"] / tot["it"],
        "wue_l_per_kwh": tot["water"] * 3.785 / tot["it"],
        "erf": tot["export"] / (tot["power"] / 1000.0),
        "heat_available_gwh": tot["avail"] / 1000.0,
        "heat_exported_gwh": tot["export"] / 1000.0,
        "heat_delivered_gwh": tot["delivered"] / 1000.0,
        "heat_stored_gwh": tot["store_in"] / 1000.0,
        "heat_rejected_gwh": tot["reject"] / 1000.0,
        "share_reused": tot["export"] / tot["avail"],
        "water_mgal": tot["water"] / 1e6,
        "cost_musd": tot["cost"] / 1e6,
        "co2_kt": tot["co2"] / 1000.0,
        "hours_tower": int(tot["hours_tower"]),
        "hours_pit_empty": int(tot["hours_pit_empty"]),
        "hours_unmet_demand": int(tot["hours_unmet"]),
    }
    if save is not False:
        out = Path("out"); out.mkdir(exist_ok=True)
        (out / f"run_{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "scenario": scenario, "site": site, "policy": pol,
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summary}, indent=2))
        pd.DataFrame([{"name": e["name"], "value": e["value"], **e["tags"]} for e in events]) \
            .to_csv(out / f"events_{run_id}.csv", index=False)

    kw_run = keewano_run(kw_rows, run_id, scenario, policy_notes, site_id=cfg["site"]["id"])
    if push if save is None else save:
        save_hourly(kw_rows, run_id, scenario, summary, pol, site=site)
    sent, push_result = 0, None
    if push:
        import keewano
        push_result = keewano.send_run(kw_run)
        sent = push_result["accepted"]
    return {"run_id": run_id, "site": site, "n_events": len(events), "n_sent": sent, "push": push_result,
            "summary": summary, "events": events, "kw_run": kw_run}


def keewano_run(kw_rows: list[dict], run_id: str, scenario: str, policy_notes: dict | None = None,
                site_id: str | None = None):
    """The run as Keewano integer events (schema.py): what actually gets ingested. The entity is
    uuid5("<site_id>:<run_id>") and a `site` event follows run_id/scenario."""
    import schema
    site_id = site_id or CFG["site"]["id"]
    return schema.Run(run_id=run_id, scenario=scenario, site_id=site_id,
                      events=schema.rows_to_events(kw_rows, run_id, scenario, policy_notes, site=site_id),
                      hourly=kw_rows)


def save_hourly(kw_rows: list[dict], run_id: str, scenario: str, summary: dict, policy: dict,
                out_dir: str = "outputs/runs", site: str = "lansing") -> Path:
    """Hourly rows in Keewano units to outputs/runs/<run_id>.csv plus an index.json entry; agent.py reads these."""
    d = Path(out_dir); d.mkdir(parents=True, exist_ok=True)
    path = d / f"{run_id}.csv"
    pd.DataFrame(kw_rows).to_csv(path, index=False)
    idx_path = d / "index.json"
    try:
        idx = json.loads(idx_path.read_text())
    except (FileNotFoundError, ValueError):
        idx = []
    idx = [e for e in idx if e.get("run_id") != run_id]
    idx.append({"run_id": run_id, "scenario": scenario, "sim": "alpine", "site": site or "lansing", "policy": policy,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "totals": summary})
    idx_path.write_text(json.dumps(idx, indent=1, default=str))
    return path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="normal", choices=list(CFG["scenarios"]) + ["all"])
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--site", default="lansing")
    a = ap.parse_args()
    for sc in (list(CFG["scenarios"]) if a.scenario == "all" else [a.scenario]):
        r = run(scenario=sc, push=a.push, save=True, site=a.site)
        print(json.dumps({k: r[k] for k in ("run_id", "n_events", "n_sent", "summary")}, indent=2))
