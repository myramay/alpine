# Ten questions a judge might ask

Each one resolves to a single Keewano query over the event schema in `docs/EVENTS.md`. Expected answers are from the first full normal-year run (`python thermal.py --scenario normal`) and the heat-wave run; they will shift slightly once the daily agent changes policy.

| # | Question | Query shape | Expected |
| --- | --- | --- | --- |
| 1 | What was the PUE for the year? | sum(power_kwh) / sum(it_kwh) | 1.10 (1.12 if the agent switches the condensers to dry) |
| 2 | How many gallons of water did the site use in the heat wave? | sum(water_gal) where scenario = heat_wave | about 1.4 million gal over the 120 h, 280,000 gal a day |
| 3 | What share of the heat was reused in January? | sum(heat_exported_mw) / sum(heat_available_mw) where month = 1 | 54% (vs 23% for the year) |
| 4 | Which hour had the highest cost, and what was the decision that hour? | max(cost_usd) with its hour and decision | hour 5373 (mid-August), about $13,600, dry coolers; no heat sales in summer |
| 5 | How many hours was the pit empty? | count where storage_soc <= 0.05 | 0 (demand never outruns supply plus the pit) |
| 6 | How many hours did the site run cooling towers? | count where heat_tower_mw > 0 | 8,760 with towers on the chiller condensers (the default); 4,534 once the agent switches to dry condensers |
| 7 | How much did water use drop after the agent capped the towers? | sum(water_gal) per day, before vs after the first policy_change | from about 279,000 gal/day to about 3,500 (98.8% less), for about $1.5M/yr more electricity |
| 8 | What was the ERF for the year? | sum(heat_exported_mw) / (sum(power_kwh)/1000) | 0.150, above Germany's 10% floor for new sites |
| 9 | What happened during the server outage? | heat_available_mw, heat_stored_mw, heat_exported_mw where scenario = server_outage | available = 0 for 6 h; the pit discharges up to 10 MW to cover demand |
| 10 | How many tonnes of CO2 did heat reuse avoid? | sum(heat_exported_mw) / 0.85 x 0.181 | about 30,600 t of boiler gas emissions |

Whole-year headline numbers (default policy, towers on the condensers): 636 GWh of heat captured, 144 GWh sold to the network (158 GWh delivered after the heat pumps), 488 GWh rejected, 102 million gallons of water (279,000 gal a day, WUE 0.44 L/kWh), net cost $97M (electricity $110/MWh minus heat sales). The agent's lever: dry condensers cut water to 1.3 million gallons for about $1.5M a year more electricity and PUE 1.10 to 1.12. Nothing in the policy touches the heat sales, so ERF stays at 15%.

## For the unexpected question

Judges will ask something not on this list. The `/ask` endpoint hands Claude the schema, these ten examples, and the Keewano query tool, so it can write a new query. Rehearsed off-list answers: cheapest month is February (heat sales highest); the cheapest hour was hour 1519 at about $7,000; average storage level in winter and hours where demand exceeded available heat are one aggregate each.
