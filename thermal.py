"""Hourly simulation and rules policy for one data center. Emits Keewano events.

Owner: Myra. Interface agreed with server.py and keewano.py:

    run(scenario="normal", push=True, policy=None) -> {"run_id", "n_events", "summary", "events"}

Each hour, in this order:
  1. available = source heat captured this hour (data/supply.csv, P50) x scenario supply_x
  2. sell    = min(demand, available) after pipe losses, through the network heat pump
  3. store   = min(available - sell, max_charge, free capacity)
  4. reject  = the rest: dry cooler up to its capacity (power only), towers above (power + water)
  5. discharge the pit when demand > available
  6. power = IT + chiller + heat pump + pumps + fans; cost and CO2 from config prices
  7. assert available == sell + store + reject (energy balance, tested in tests/test_balance.py)

Policy knobs the daily agent can move (see agent.py): prefer_dry (bool), storage_reserve (0-1),
tower_cap_mw (float). Defaults in DEFAULT_POLICY.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

CFG = yaml.safe_load(Path("config/site.yaml").read_text())
DEFAULT_POLICY = {"prefer_dry": True, "storage_reserve": 0.2, "tower_cap_mw": 1e9}

EVENT_NAMES = [
    "heat_available_mw", "heat_exported_mw", "heat_stored_mw", "heat_rejected_mw",
    "water_gal", "power_kwh", "it_kwh", "cost_usd", "co2_t", "decision", "storage_soc",
]


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


def run(scenario: str = "normal", push: bool = True, policy: dict | None = None) -> dict:
    # TODO(Myra): implement the hourly loop described in the module docstring and emit one
    # event per EVENT_NAMES per hour, tagged hour/day/month/scenario/run_id. Then, if push,
    # keewano.send(events, run_id). Write out/run_<run_id>.json with summary (pue, wue_l_per_kwh,
    # erf, water_gal, cost_usd, co2_t, hours_tower, hours_pit_empty) for the stub report.
    raise NotImplementedError("thermal.run: see docstring")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="normal", choices=list(CFG["scenarios"]))
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    r = run(scenario=a.scenario, push=a.push)
    print(json.dumps({k: r[k] for k in ("run_id", "n_events", "summary")}, indent=2))
