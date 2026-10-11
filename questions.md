# Ten questions a judge might ask

Each one should resolve to a single Keewano query over the event schema in `docs/EVENTS.md`. The "expected" column is filled in after the first full-year run; until then it says what shape the answer takes.

| # | Question | Query shape | Expected (fill after run) |
| --- | --- | --- | --- |
| 1 | What was the PUE for the year? | sum(power_kwh) / sum(it_kwh) | around 1.1-1.2 |
| 2 | How many gallons of water did the site use in the heat wave? | sum(water_gal) where scenario = heat_wave | |
| 3 | What share of the heat was reused in January? | sum(heat_exported_mw) / sum(heat_available_mw) where month = 1 | |
| 4 | Which hour had the highest cost, and what was the decision that hour? | max(cost_usd) with its hour and decision | |
| 5 | How many hours was the pit empty? | count where storage_soc <= 0.05 | |
| 6 | How many hours did the site run cooling towers? | count where decision = reject_tower | |
| 7 | How much did water use drop after the agent raised the dry-cooler preference? | sum(water_gal) per day, before vs after the first policy_change | |
| 8 | What was the ERF for the year? | sum(heat_exported_mw) / (sum(power_kwh)/1000) | |
| 9 | What happened during the server outage? | heat_available_mw, heat_stored_mw, heat_exported_mw where scenario = server_outage | pit discharges to cover demand |
| 10 | How many tonnes of CO2 did heat reuse avoid? | sum(co2_t) difference vs a reject-only baseline, or sum(heat_exported_mw x 0.181 / boiler efficiency) | |

## For the unexpected question

Judges will ask something not on this list. The `/ask` endpoint hands Claude the schema above, these ten examples, and the Keewano query tool, so it can write a new query. Rehearse three off-list questions tonight: "which month was cheapest", "average storage level in winter", "how many hours was demand higher than available heat".
