# Judge questions

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
