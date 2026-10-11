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


def load_inputs() -> pd.DataFrame:
    s = pd.read_csv("data/supply.csv", parse_dates=["timestamp"])
    d = pd.read_csv("data/demand.csv", parse_dates=["timestamp"])
    df = s.merge(d, on="timestamp")
    df["hour"] = range(len(df))
    df["day"] = df["hour"] // 24
    df["month"] = df["timestamp"].dt.month
    return df


def cop(t_source_c: float, t_sink_c: float, carnot_fraction: float) -> float:
    """Heating COP = carnot_fraction x T_sink / (T_sink - T_source), temperatures in K."""
    ts, tk = t_source_c + 273.15, t_sink_c + 273.15
    return max(1.0, carnot_fraction * tk / max(tk - ts, 1.0))


def _scenario_window(scenario: str) -> tuple[int, int, dict]:
    sc = CFG["scenarios"].get(scenario) or {}
    return sc.get("start_hour", -1), sc.get("hours", 0), sc


def run(scenario: str = "normal", push: bool = True, policy: dict | None = None,
        policy_by_day: dict[int, dict] | None = None, save: bool = True) -> dict:
    """Simulate a typical year. policy_by_day lets agent.py change the policy from a given day on.
    save=False (what-if runs from /compare and /sensitivity) writes nothing to out/."""
    df = load_inputs()
    c, hn, st, pr, em = CFG["cooling"], CFG["heat_network"], CFG["storage"], CFG["prices"], CFG["emissions"]
    dc = CFG["data_center"]
    start, nh, sc = _scenario_window(scenario)
    pol = dict(DEFAULT_POLICY, **(policy or {}))
    policy_by_day = policy_by_day or {}

    deliver_frac = 1.0 - hn["pipe_loss_per_km"] * hn["route_km"]
    cap = st["capacity_mwh"]
    soc = st["initial_soc"]
    run_id = f"{scenario}-{datetime.now(timezone.utc):%m%d%H%M}-{uuid.uuid4().hex[:4]}"
    events: list[dict] = []
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
    if save:
        out = Path("out"); out.mkdir(exist_ok=True)
        (out / f"run_{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "scenario": scenario, "policy": pol,
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summary}, indent=2))
        pd.DataFrame([{"name": e["name"], "value": e["value"], **e["tags"]} for e in events]) \
            .to_csv(out / f"events_{run_id}.csv", index=False)

    sent = 0
    if push:
        import keewano
        sent = keewano.send(events, run_id)
    return {"run_id": run_id, "n_events": len(events), "n_sent": sent, "summary": summary, "events": events}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="normal", choices=list(CFG["scenarios"]))
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    r = run(scenario=a.scenario, push=a.push)
    print(json.dumps({k: r[k] for k in ("run_id", "n_events", "n_sent", "summary")}, indent=2))
