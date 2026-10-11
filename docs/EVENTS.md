# Keewano event schema

One event = a name and one typed value. Entity id is `lansing:<run_id>`. Every event carries these tags: `hour` (0-8759), `day` (0-364), `month` (1-12), `scenario` (normal, heat_wave, cold_snap, server_outage), `run_id`.

| Event name | Type | Unit | Written by |
| --- | --- | --- | --- |
| `heat_available_mw` | float | MW thermal captured from the servers this hour | thermal.py |
| `heat_exported_mw` | float | MW thermal delivered to the heat network | thermal.py |
| `heat_stored_mw` | float | MW thermal into the pit (negative = discharge) | thermal.py |
| `heat_rejected_mw` | float | MW thermal rejected to air (dry cooler + tower) | thermal.py |
| `heat_tower_mw` | float | MW thermal rejected through evaporative towers this hour: captured-heat overflow plus chiller condenser heat when the policy is not `prefer_dry`. The part that uses water. | thermal.py |
| `water_gal` | float | gallons evaporated this hour (towers only) | thermal.py |
| `power_kwh` | float | total facility kWh this hour: IT + cooling + pumps + heat pumps | thermal.py |
| `it_kwh` | float | IT kWh this hour | thermal.py |
| `cost_usd` | float | net cost this hour: electricity + water - heat sales | thermal.py |
| `co2_t` | float | tonnes CO2 this hour, net of boiler gas avoided | thermal.py |
| `decision` | string | dominant path this hour: `reject_dry`, `reject_tower`, `sell`, `store` | thermal.py |
| `storage_soc` | float | pit state of charge, 0 to 1 | thermal.py |
| `policy_change` | string | what the agent changed today and why | agent.py |

## The three report metrics, as queries over these events

- **PUE** = sum(power_kwh) / sum(it_kwh)
- **WUE** (L/kWh) = sum(water_gal) x 3.785 / sum(it_kwh)
- **ERF** (energy reuse factor, the German EnEfG measure) = sum(heat_exported_mw) / (sum(power_kwh) / 1000)

## Why this shape

- One value per event keeps every question a single aggregate: "water in heat-wave hours" is `sum(water_gal) where scenario = heat_wave`.
- `decision` and `policy_change` as strings make the agent's behavior queryable: "how many hours did it choose towers after the agent raised the dry-cooler preference?"
- `run_id` on everything means re-runs never mix and a judge's question always hits one year.

## On the wire to Keewano

Keewano stores one typed value per event with no float type (uint, string, bool, price_usd_cent), so `thermal.run(push=True)` sends the integer catalog in `schema.py`. Names map like this:

| Above | In Keewano |
| --- | --- |
| `heat_available_mw`, `heat_exported_mw`, `heat_rejected_mw` | `heat_available_kw`, `heat_exported_kw`, `heat_rejected_kw` (x1000) |
| `heat_stored_mw` (signed) | `heat_stored_kw` (charge) and `heat_discharged_kw` (discharge) |
| `heat_tower_mw` | `reject_tower_kw`; dry is `reject_dry_kw` (both include chiller condenser heat) |
| `water_gal`, `power_kwh`, `it_kwh` | same names, rounded to integers |
| `cost_usd` (net) | `cost_usd` in cents (signed), plus `electricity_cost_usd`, `water_cost_usd`, `heat_revenue_usd` |
| `co2_t` (net) | `co2_kg` (grid, gross) and `co2_avoided_kg`; net = co2_kg - co2_avoided_kg |
| `storage_soc` (0-1) | `storage_soc_pct` (0-100) |
| `decision` | `decision` |
| tags `hour`, `day`, `month`, `scenario`, `run_id` | context events emitted before each hour's measures (step functions) |
| the policy | `policy_prefer_dry`, `policy_storage_reserve_pct`, `policy_tower_cap_mw` every hour; `policy_change` at hour 0 of a changed day |

Also: `demand_kw`, `unmet_demand_kw`, `heat_delivered_kw`, `cooling_kwh`, `heat_pump_kwh`, `pump_kwh`, `ambient_k`. Report queries in Keewano: PUE `sum(power_kwh)/sum(it_kwh)`, WUE `3.785*sum(water_gal)/sum(it_kwh)`, ERF `sum(heat_exported_kw)/sum(power_kwh)`.
