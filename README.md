# Thermal Layer

Decides every hour where a data center's heat goes (sell it to a heat network, store it, or reject it through dry coolers or towers), streams each decision and its cost, water, power and carbon into Keewano, and answers any plain-English question about the site from that data. PUE, WUE and ERF are computed live by Keewano queries, and the query is shown under each number.

Demo site: the 100 MW liquid-cooled TeraWulf site at Lansing, NY, on Cayuga Lake. Typical year, hourly.

## Run

```bash
uv venv --python 3.11 && source .venv/bin/activate && uv pip install -r requirements.txt
cp .env.example .env                      # fill in the keys
python keewano.py check                   # which keys are set; tries each connection
python keewano.py ping                    # one event round trip
python thermal.py --scenario normal --push   # simulate the year and ingest it (~1 min)
python thermal.py --scenario all --push      # heat_wave, cold_snap, server_outage
# wait ~5 min for Keewano to make the events queryable
python agent.py --run <normal_run_id>     # daily agent: asks Keewano about each day, sets policy, re-runs as "agent"
uvicorn server:app --port 8000            # /ask /report /decisions /runs /run /health
pytest -q
```

## Files

| File | What it does |
|---|---|
| `schema.py` | Keewano event catalog (30 custom events, integer units), shared dataclasses |
| `config/site.yaml` | About a dozen constants retyped from HeatOS `lansing.yaml`, each with its source note |
| `thermal.py` | Hourly simulation and rules policy, producing events |
| `keewano.py` | Batch ingest (REST), SQL (Postgres wire), MCP client, report queries |
| `agent.py` | Daily policy agent and `ask()` (Claude with the Keewano MCP tools) |
| `server.py` | FastAPI backend for the web page |
| `questions.md` | 10 judge questions, with the query that answers each |

## Data provenance

Taken from the HeatOS team's trained models as CSV outputs (nothing retrained, no HeatOS code imported):

- `data/supply.csv` comes from `outputs/models/site2_supply_hourly_typical_year.csv`, using the phase 1 AI-inference P50 columns (IT MW, captured heat MW).
- `data/demand.csv` comes from `outputs/models/site2_demand_hourly_typical_year.csv`, using outdoor temperature P50 and network demand within 15 km at P50. The underlying data is NOAA Ithaca weather plus the Lansing parcel and OpenStreetMap customer pipeline.
- `outputs/demand_model_validation.txt` holds the demand model's held-out validation.

Storage, dry cooler capacity and heat price are marked "assumption - verify" in `config/site.yaml`.

## Keewano model

Keewano has no float type, so quantities are stored as unsigned integers (kW, kWh, gallons, kg) and money as cents. Each run is one Keewano entity, identified by `uuid5(site:run_id)`. Columns are step functions: `run_id`, `scenario`, `day`, `month`, `hour` and `decision` hold forward until they next change, so `WHERE run_id = 'normal-ab12cd' AND month = 1` scopes every measure in January of that run.

Keewano takes about 5 minutes to make new events queryable. Because of that, the daily agent reads each day of the ingested baseline run from Keewano, sets the next day's policy, and then re-simulates the year as the `agent` run. It does not query its own run live, hour by hour.
