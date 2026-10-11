# Keewano event schema

One event = a name and one typed value. Entity id is `lansing:<run_id>`. Every event carries these tags: `hour` (0-8759), `day` (0-364), `month` (1-12), `scenario` (normal, heat_wave, cold_snap, server_outage), `run_id`.

| Event name | Type | Unit | Written by |
| --- | --- | --- | --- |
| `heat_available_mw` | float | MW thermal captured from the servers this hour | thermal.py |
| `heat_exported_mw` | float | MW thermal delivered to the heat network | thermal.py |
| `heat_stored_mw` | float | MW thermal into the pit (negative = discharge) | thermal.py |
| `heat_rejected_mw` | float | MW thermal rejected to air (dry cooler + tower) | thermal.py |
| `heat_tower_mw` | float | MW thermal rejected through evaporative towers this hour: captured-heat overflow plus chiller condenser heat when the policy is not `prefer_dry`. The part that uses water. | thermal.py |
| `heat_dry_mw` | float | MW thermal rejected through dry coolers and dry condensers this hour (fan power, no water) | thermal.py |
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
