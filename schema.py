"""Shared contract: the Keewano event catalog and the shapes passed between modules.

Keewano has no float type, so every quantity is an unsigned integer in a small unit
(kW, kWh, gallons, kg) and money is price_usd_cent. Names are lowercase snake_case so
SQL needs no quoting. Columns in Keewano are step functions: a context event (hour,
day, month, decision) holds until the next one, so context is emitted before the
measures of each hour and `WHERE day = 41` scopes every measure in that day.

One Keewano entity (installId) per run: uuid5(NAMESPACE_URL, f"{site_id}:{run_id}").
Hour h is stamped at BASE_TS + h*3600 (+ i seconds to keep in-hour order).
"""
from __future__ import annotations

import json
import uuid
import zlib
from dataclasses import dataclass, field

BASE_TS = 1735689600  # 2025-01-01T00:00:00Z, typical year mapped onto 2025

# (eventTypeId, name, dataType, unit/meaning)
EVENTS = [
    (2000, "run_id", "string", "run id, e.g. normal-7f3a; first event of a run"),
    (2001, "scenario", "string", "normal | heat_wave | cold_snap | server_outage | agent"),
    (2002, "hour", "uint", "hour of year 0..8759, emitted every hour"),
    (2003, "day", "uint", "day of year 0..364, emitted when it changes"),
    (2004, "month", "uint", "1..12, emitted when it changes"),
    (2005, "ambient_k", "uint", "outdoor temperature, kelvin (C = value - 273)"),
    (2006, "decision", "string", "dominant heat path this hour: sell | store | reject_dry | reject_tower"),
    (2007, "heat_available_kw", "uint", "heat captured from liquid loop, kW thermal"),
    (2008, "demand_kw", "uint", "heat network demand, kW thermal"),
    (2009, "heat_exported_kw", "uint", "heat sold to network, kW thermal (incl. pit discharge)"),
    (2010, "heat_stored_kw", "uint", "pit charge, kW thermal"),
    (2011, "heat_discharged_kw", "uint", "pit discharge to network, kW thermal"),
    (2012, "heat_rejected_kw", "uint", "total heat rejected, kW thermal"),
    (2013, "reject_dry_kw", "uint", "heat rejected via dry coolers (power only, no water)"),
    (2014, "reject_tower_kw", "uint", "heat rejected via cooling towers (power + water)"),
    (2015, "unmet_demand_kw", "uint", "network demand not served, kW thermal"),
    (2016, "water_gal", "uint", "water used this hour, gallons"),
    (2017, "it_kwh", "uint", "IT energy this hour, kWh"),
    (2018, "cooling_kwh", "uint", "chiller/dry cooler/tower electricity this hour, kWh"),
    (2019, "heat_pump_kwh", "uint", "heat pump electricity this hour, kWh"),
    (2020, "pump_kwh", "uint", "network pumping electricity this hour, kWh"),
    (2021, "power_kwh", "uint", "total facility energy this hour: IT+cooling+heat pumps+pumps+misc"),
    (2022, "cost_usd", "price_usd_cent", "electricity cost this hour (cents)"),
    (2023, "heat_revenue_usd", "price_usd_cent", "heat sale revenue this hour (cents)"),
    (2024, "co2_kg", "uint", "grid CO2 from facility electricity this hour, kg"),
    (2025, "co2_avoided_kg", "uint", "gas boiler CO2 avoided by exported heat, kg"),
    (2026, "storage_soc_pct", "uint", "pit state of charge after this hour, 0..100"),
    (2027, "policy_change", "string", "agent policy change + reason, <=256 chars"),
    (2028, "policy_dry_boost_pct", "uint", "active dry_boost policy, percent"),
    (2029, "policy_discharge_floor_pct", "uint", "active discharge floor policy, percent SOC"),
]

EVENT_ID = {name: eid for eid, name, _, _ in EVENTS}
EVENT_TYPE = {name: dt for _, name, dt, _ in EVENTS}
CUSTOM_EVENTS = [{"eventTypeId": eid, "name": name, "dataType": dt} for eid, name, dt, _ in EVENTS]
EVENTS_HASH = zlib.crc32(json.dumps(CUSTOM_EVENTS, sort_keys=True).encode()) or 1

# Per-hour emit order. Context first so it scopes the measures that follow.
HOURLY_CONTEXT = ["hour", "ambient_k", "decision", "policy_dry_boost_pct", "policy_discharge_floor_pct"]
HOURLY_MEASURES = [
    "heat_available_kw", "demand_kw", "heat_exported_kw", "heat_stored_kw", "heat_discharged_kw",
    "heat_rejected_kw", "reject_dry_kw", "reject_tower_kw", "unmet_demand_kw", "water_gal",
    "it_kwh", "cooling_kwh", "heat_pump_kwh", "pump_kwh", "power_kwh", "cost_usd",
    "heat_revenue_usd", "co2_kg", "co2_avoided_kg", "storage_soc_pct",
]


def entity_id(site_id: str, run_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{site_id}:{run_id}"))


@dataclass
class Event:
    """One Keewano event. `value` is already the integer/string Keewano expects."""
    name: str
    value: int | str | None
    ts: int  # unix seconds

    def to_json(self) -> dict:
        return {"eventTypeId": EVENT_ID[self.name], "dataType": EVENT_TYPE[self.name],
                "timestamp": self.ts, "data": self.value}


@dataclass
class Policy:
    """What the daily agent is allowed to move. thermal.py reads it per day."""
    dry_boost: float = 0.0        # 0..0.3
    discharge_floor: float = 0.05 # 0.05..0.6
    precharge: bool = False


@dataclass
class Run:
    run_id: str
    scenario: str
    site_id: str
    events: list[Event] = field(default_factory=list)
    hourly: list[dict] = field(default_factory=list)  # float-precision rows for tests/local checks
