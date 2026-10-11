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

## Live Keewano column names

The query shapes above use the float names from `docs/EVENTS.md`. In Keewano the columns are integers (Keewano has no floats; see `schema.py`): `heat_*_mw` becomes `heat_*_kw` (x1000), `heat_tower_mw` becomes `reject_tower_kw`, `storage_soc` (0 to 1) becomes `storage_soc_pct` (0 to 100), `co2_t` becomes `co2_kg` (gross grid CO2; `co2_avoided_kg` is separate), and `cost_usd` is in cents. The SQL below is written against those live columns.

## Ten questions as live Keewano SQL

Ten questions we expect to be asked, the Keewano SQL that answers each, and the
expected answer (fill in after the runs are ingested; check against the local
`outputs/runs/<run_id>.csv` totals).

Placeholders: `<baseline>` = the normal-scenario run id, `<agent>` = the agent run id
(both listed in `outputs/runs/index.json`). Each run is one Keewano entity, so
`WHERE run_id = '...'` (or `scenario = '...'`) selects one whole run.

Calendar: day is 0..364, hour is 0..8759 (hour = day*24 + hour_of_day).
Heat wave = Jul 15 + 120 h = hours 4680..4799 (days 195..199).
Cold snap = Jan 20 + 72 h = hours 456..527 (days 19..21).
kW columns are hourly averages, so `sum(x_kw)` over hours = kWh.

## 1. How much water did the site use during the heat wave?

```sql
SELECT run_id, sum(water_gal) AS water_gal
FROM events
WHERE scenario = 'heat_wave' AND hour BETWEEN 4680 AND 4799
GROUP BY run_id
```

Expected answer: ______ gal (______ L)

## 2. What share of the captured heat was reused (sold to the network) in January?

```sql
SELECT sum(heat_exported_kw) AS exported_kwh,
       sum(heat_available_kw) AS available_kwh,
       100.0 * sum(heat_exported_kw) / sum(heat_available_kw) AS reuse_pct
FROM events
WHERE run_id = '<baseline>' AND month = 1
```

Expected answer: ______ %

## 3. Which hour had the highest electricity cost, and why?

```sql
SELECT hour,
       sum(cost_usd) AS cost,
       sum(power_kwh) AS power_kwh,
       sum(cooling_kwh) AS cooling_kwh,
       sum(heat_pump_kwh) AS heat_pump_kwh,
       sum(demand_kw) AS demand_kw,
       max(ambient_k) AS ambient_k
FROM events
WHERE run_id = '<baseline>'
GROUP BY hour
ORDER BY cost DESC
LIMIT 1
```

Expected answer: hour ______ (day ______), $______; driver: ______

## 4. How many hours was the storage pit empty?

```sql
SELECT count(storage_soc_pct) FILTER (WHERE storage_soc_pct <= 5) AS empty_hours
FROM events
WHERE run_id = '<baseline>'
```

Expected answer: ______ h

## 5. What was the PUE for the year?

```sql
SELECT sum(power_kwh) AS facility_kwh,
       sum(it_kwh) AS it_kwh,
       1.0 * sum(power_kwh) / sum(it_kwh) AS pue
FROM events
WHERE run_id = '<baseline>'
```

Expected answer: PUE ______

## 6. How much water did the agent save compared with the baseline?

```sql
SELECT scenario, run_id, sum(water_gal) AS water_gal, sum(power_kwh) AS power_kwh
FROM events
WHERE scenario IN ('normal', 'agent')
GROUP BY scenario, run_id
```

Expected answer: ______ gal saved (______ %), at a cost of ______ kWh extra power

## 7. How much heat demand went unmet during the cold snap?

```sql
SELECT run_id,
       sum(unmet_demand_kw) AS unmet_kwh,
       count(unmet_demand_kw) FILTER (WHERE unmet_demand_kw > 0) AS unmet_hours
FROM events
WHERE scenario = 'cold_snap' AND hour BETWEEN 456 AND 527
GROUP BY run_id
```

Expected answer: ______ kWh over ______ h

## 8. When did the cooling towers work hardest?

```sql
SELECT hour,
       sum(reject_tower_kw) AS tower_kw,
       sum(water_gal) AS water_gal,
       max(ambient_k) AS ambient_k
FROM events
WHERE run_id = '<baseline>'
GROUP BY hour
ORDER BY tower_kw DESC
LIMIT 1
```

Expected answer: hour ______, ______ kW to towers, ______ gal, ______ C

## 9. How much CO2 did heat reuse avoid this year?

```sql
SELECT sum(co2_avoided_kg) AS co2_avoided_kg, sum(co2_kg) AS co2_emitted_kg
FROM events
WHERE run_id = '<baseline>'
```

Expected answer: ______ t avoided (vs ______ t emitted)

## 10. How much revenue did heat sales bring in?

```sql
SELECT sum(heat_revenue_usd) AS heat_revenue, sum(cost_usd) AS electricity_cost
FROM events
WHERE run_id = '<baseline>'
```

Expected answer: $______ (electricity cost $______). Note: these are `price_usd_cent`
columns, which Keewano renders as a double; check on the first query whether it shows
dollars or cents by comparing with the local CSV.
