"""Thermal Layer basic sim (kept from the first build; Alpine's thermal.py is the main sim).

Each hour the captured heat goes to one of four paths: sell (district network),
store (pit), reject_dry (dry coolers, power only) or reject_tower (towers, power +
water). Produces float rows for local checks and integer Keewano events (schema.py).
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import yaml

import schema
from schema import BASE_TS, HOURLY_CONTEXT, HOURLY_MEASURES, Event, Policy, Run

ROOT = os.path.dirname(os.path.abspath(__file__))
SCENARIOS = ["normal", "heat_wave", "cold_snap", "server_outage"]
TOL = 1e-6


def load_config(path="config/site_basic.yaml") -> dict:
    if not os.path.isabs(path) and not os.path.exists(path):
        path = os.path.join(ROOT, path)
    with open(path) as f:
        return yaml.safe_load(f)


_DATA = {}


def _inputs():
    if not _DATA:
        s = pd.read_csv(os.path.join(ROOT, "data/supply_basic.csv"))
        d = pd.read_csv(os.path.join(ROOT, "data/demand_basic.csv"))
        _DATA.update(it=s.it_mw.tolist(), avail=s.heat_available_mw.tolist(),
                     T=d.outdoor_c.tolist(), demand=d.demand_mw.tolist())
    return {k: list(v) for k, v in _DATA.items()}


def _window(sc: dict) -> range:
    start = datetime(2025, sc["month"], sc["start_day_of_month"]) - datetime(2025, 1, 1)
    h0 = start.days * 24
    return range(h0, h0 + int(sc["hours"]))


def scenario_window(scenario: str, cfg: dict | None = None) -> range:
    cfg = cfg or load_config()
    sc = cfg["scenarios"].get(scenario)
    return _window(sc) if sc else range(0)


def _apply_scenario(x: dict, scenario: str, cfg: dict) -> None:
    sc = cfg["scenarios"].get(scenario)
    if not sc:
        return
    for h in _window(sc):
        x["demand"][h] *= sc.get("demand_mult", 1.0)
        x["T"][h] += sc.get("ambient_add_c", 0.0)
        m = sc.get("available_mult", 1.0)
        x["avail"][h] *= m
        x["it"][h] *= m  # outage: servers are down, not just the heat loop


def simulate(scenario="normal", run_id=None, policies=None, policy_notes=None,
             cfg=None, site_id=None) -> Run:
    cfg = cfg or load_config()
    site_id = site_id or cfg["site"]["id"]
    run_id = run_id or f"{scenario}-{uuid.uuid4().hex[:6]}"
    policies, policy_notes = policies or {}, policy_notes or {}
    run = Run(run_id=run_id, scenario=scenario, site_id=site_id)
    x = _inputs()
    _apply_scenario(x, scenario, cfg)

    c, hp, st, pm = cfg["cooling"], cfg["heat_pumps"], cfg["storage"], cfg["pumping"]
    pr, em = cfg["prices"], cfg["emissions"]
    src_t = cfg["site"]["supply_temp_c"]
    cop, cap = c["chiller_cop"], st["capacity_mwh"]
    keep = 1.0 - pm["pipe_loss_per_km"] * pm["network_length_km"]
    pd_ = cfg["policy_defaults"]
    pol = Policy(pd_["dry_boost"], pd_["discharge_floor"], bool(pd_["precharge"]))
    soc = st["initial_soc"]
    day_month = [(datetime(2025, 1, 1) + timedelta(days=d)).month for d in range(366)]
    ev, rows = run.events, run.hourly
    last_day = last_month = None

    for h in range(len(x["avail"])):
        day = h // 24
        month = day_month[day]
        if h % 24 == 0 and day in policies:
            pol = policies[day]
        avail, demand, T, it = x["avail"][h], x["demand"][h], x["T"][h], x["it"][h]

        # --- dispatch ---
        demand_gross = demand / keep
        free = max(0.0, (1.0 - soc) * cap)
        if pol.precharge and soc < 0.5:
            store = min(avail, st["max_charge_mw"], free)
            export_live = min(demand_gross, avail - store)
        else:
            export_live = min(demand_gross, avail)
            store = min(avail - export_live, st["max_charge_mw"], free)
        shortfall = demand_gross - export_live
        discharge = 0.0
        if shortfall > 0 and store == 0:
            discharge = min(shortfall, st["max_discharge_mw"], max(0.0, (soc - pol.discharge_floor) * cap))
        exported = export_live + discharge
        delivered = exported * keep
        unmet = max(0.0, demand - delivered)
        soc = (soc * cap + store - discharge) / cap * (1.0 - st["loss_per_hour"])

        # --- rejection ---
        reject = max(0.0, avail - export_live - store)
        derate = 1.0 - (1.0 - c["dry_derate_floor"]) * min(1.0, max(0.0, (T - c["dry_derate_start_c"]) / c["dry_derate_span_c"]))
        dry_base = c["dry_capacity_mw"] * derate
        reject_dry = min(reject, dry_base * (1.0 + pol.dry_boost))
        reject_tower = reject - reject_dry
        boosted = max(0.0, reject_dry - dry_base)
        assert abs(avail - (export_live + store + reject_dry + reject_tower)) < TOL, f"balance h={h}"
        air = max(0.0, it - avail)
        tower_kwh = reject_tower / cop * 1000
        cooling_kwh = ((reject_dry - boosted) + boosted * c["dry_boost_power_mult"] + air) / cop * 1000 + tower_kwh
        water_gal = tower_kwh * c["water_gal_per_kwh"]

        # --- heat pump, pumps, totals ---
        f = min(1.0, max(0.0, (T + 15.0) / 30.0))
        t_net = hp["network_temp_cold_c"] + f * (hp["network_temp_warm_c"] - hp["network_temp_cold_c"])
        if t_net <= src_t - hp["direct_use_margin_c"]:
            hp_kwh = 0.0
        else:
            lift = t_net - src_t
            hcop = hp["max_cop"] if lift <= 0 else min(hp["max_cop"], hp["eta"] * (t_net + 273.15) / lift)
            hp_kwh = exported * 1000 / hcop
        pump_kwh = pm["design_power_kw"] * exported / pm["design_flow_mw"]
        it_kwh, misc_kwh = it * 1000, c["misc_overhead"] * it * 1000
        power_kwh = it_kwh + cooling_kwh + hp_kwh + pump_kwh + misc_kwh
        cost = power_kwh / 1000 * pr["electricity_usd_per_mwh"]
        revenue = delivered * pr["heat_sale_usd_per_mwh"]
        co2 = power_kwh / 1000 * em["grid_t_per_mwh"]
        co2_av = delivered / em["gas_boiler_eff"] * em["gas_boiler_t_per_mwh"]
        paths = {"sell": exported, "store": store, "reject_dry": reject_dry, "reject_tower": reject_tower}
        decision = max(paths, key=paths.get)

        rows.append(dict(hour=h, day=day, month=month, outdoor_c=T, it_mw=it, avail_mw=avail,
                         demand_mw=demand, export_mw=exported, export_live_mw=export_live,
                         delivered_mw=delivered, store_mw=store, discharge_mw=discharge,
                         reject_mw=reject, reject_dry_mw=reject_dry, reject_tower_mw=reject_tower,
                         unmet_mw=unmet, water_gal=water_gal, it_kwh=it_kwh, cooling_kwh=cooling_kwh,
                         hp_kwh=hp_kwh, pump_kwh=pump_kwh, misc_kwh=misc_kwh, power_kwh=power_kwh,
                         cost_usd=cost, heat_revenue_usd=revenue, co2_t=co2, co2_avoided_t=co2_av,
                         soc=soc, unmet_demand_kw=unmet * 1000, storage_soc_pct=soc * 100,
                         dry_boost=pol.dry_boost, discharge_floor=pol.discharge_floor,
                         decision=decision))

        # --- events ---
        ts0 = BASE_TS + h * 3600
        out = []
        if h == 0:
            out += [("run_id", run_id), ("scenario", scenario)]
        if day != last_day:
            out.append(("day", day))
            last_day = day
        if month != last_month:
            out.append(("month", month))
            last_month = month
        ctx = {"hour": h, "ambient_k": T + 273.15, "decision": decision,
               "policy_dry_boost_pct": pol.dry_boost * 100, "policy_discharge_floor_pct": pol.discharge_floor * 100}
        out += [(k, ctx[k]) for k in HOURLY_CONTEXT]
        if h % 24 == 0 and day in policy_notes:
            out.append(("policy_change", str(policy_notes[day])[:256]))
        kw = 1000
        meas = {"heat_available_kw": avail * kw, "demand_kw": demand * kw, "heat_exported_kw": exported * kw,
                "heat_stored_kw": store * kw, "heat_discharged_kw": discharge * kw, "heat_rejected_kw": reject * kw,
                "reject_dry_kw": reject_dry * kw, "reject_tower_kw": reject_tower * kw, "unmet_demand_kw": unmet * kw,
                "water_gal": water_gal, "it_kwh": it_kwh, "cooling_kwh": cooling_kwh, "heat_pump_kwh": hp_kwh,
                "pump_kwh": pump_kwh, "power_kwh": power_kwh, "cost_usd": cost * 100,
                "heat_revenue_usd": revenue * 100, "co2_kg": co2 * 1000, "co2_avoided_kg": co2_av * 1000,
                "storage_soc_pct": soc * 100}
        out += [(k, meas[k]) for k in HOURLY_MEASURES]
        for i, (name, v) in enumerate(out):
            if not isinstance(v, str):
                v = max(0, int(round(v)))
            ev.append(Event(name, v, ts0 + i))
    return run


def summarize(run: Run) -> dict:
    r = pd.DataFrame(run.hourly)
    it, power = r.it_kwh.sum(), r.power_kwh.sum()
    return {
        "pue": round(power / it, 4),
        "wue_l_per_kwh": round(r.water_gal.sum() * 3.78541 / it, 4),
        "erf": round(r.delivered_mw.sum() * 1000 / power, 4),
        "water_gal": round(r.water_gal.sum()),
        "unmet_mwh": round(r.unmet_mw.sum(), 1),
        "heat_sold_mwh": round(r.delivered_mw.sum(), 1),
        "cost_usd": round(r.cost_usd.sum()),
        "heat_revenue_usd": round(r.heat_revenue_usd.sum()),
        "co2_t": round(r.co2_t.sum(), 1),
        "co2_avoided_t": round(r.co2_avoided_t.sum(), 1),
    }


def save_run(run: Run, out_dir="outputs/runs") -> str:
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(ROOT, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{run.run_id}.csv")
    pd.DataFrame(run.hourly).to_csv(path, index=False)
    idx_path = os.path.join(out_dir, "index.json")
    try:
        with open(idx_path) as f:
            idx = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        idx = []
    idx.append({"run_id": run.run_id, "scenario": run.scenario,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "totals": summarize(run)})
    with open(idx_path, "w") as f:
        json.dump(idx, f, indent=1)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Thermal Layer heat-dispatch sim")
    ap.add_argument("--scenario", default="normal", choices=SCENARIOS + ["all"])
    ap.add_argument("--push", action="store_true", help="stream events to Keewano")
    a = ap.parse_args(argv)
    for sc in SCENARIOS if a.scenario == "all" else [a.scenario]:
        t = time.time()
        run = simulate(sc)
        s = summarize(run)
        path = save_run(run)
        print(f"{run.run_id:22s} PUE {s['pue']:.3f}  WUE {s['wue_l_per_kwh']:.3f} L/kWh  ERF {s['erf']:.3f}  "
              f"water {s['water_gal']:,} gal  unmet {s['unmet_mwh']:,} MWh  cost ${s['cost_usd']:,}  "
              f"({len(run.events):,} events, {time.time() - t:.1f}s) -> {os.path.relpath(path, ROOT)}")
        if a.push:
            import keewano
            keewano.send_run(run)


if __name__ == "__main__":
    main()
