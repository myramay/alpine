# Alpine

The thermal layer for data centers. Every hour, an agent decides where a site's heat goes: reject it (dry coolers or cooling towers), sell it to a heat network, or store it. Every decision and its cost, water, power and carbon streams into Keewano. Anyone can ask the site a question in plain English and get the answer plus the query that produced it. The Report view shows PUE, WUE and ERF computed live, with the query under each number.

Built at SpeedHacks, Oct 11, 2026. Theme: give people time back. The bottleneck we remove: a data center's sustainability or operations team spends weeks each quarter pulling hourly cooling, water and power logs into spreadsheets to report PUE, WUE and heat reuse, and nobody can trace a number.

## What's in here

| Path | What |
| --- | --- |
| `thermal.py` | Hourly simulation and rules policy. Emits events. |
| `keewano.py` | Batch event sender, tagged by `run_id`. |
| `agent.py` | Daily loop: asks Keewano about yesterday, sets today's policy. Also the free-text `/ask`. |
| `server.py` | FastAPI: `/run`, `/ask`, `/report`, `/decisions`. |
| `web/index.html` | Ask page, Report page, decision log. |
| `config/site.yaml` | Every constant with a source note. |
| `data/` | Supply and demand model outputs (see below). |
| `questions.md` | 10 questions a judge might ask, with expected answers. |
| `docs/EVENTS.md` | The Keewano event schema. |

## Where the data comes from

The hourly supply and demand curves are outputs of two trained models: a multi-source compute-load trace model (7 public trace sources, 134 domains) for supply, and a quantile demand model trained on NYC LL84 building data and NREL ComStock/ResStock profiles for demand, run for the Lansing, NY site on NOAA Ithaca weather. `data/MODEL_VALIDATION.txt` is their held-out validation.

- `data/supply.csv`: phase 1, AI inference, 100 MW IT. Columns: IT MW, source heat MW, delivered heat MW, heat-pump electricity MW, COP, each at P10/P50/P90.
- `data/demand.csv`: heat demand from buildings within 15 km, P10/P50/P90, plus outdoor temperature.

## Run

```
uv venv --python 3.11 && source .venv/bin/activate && uv pip install -r requirements.txt
cp .env.example .env   # fill in keys
python thermal.py --scenario normal --push
python agent.py --run <run_id>
uvicorn server:app --port 8000   # then open http://localhost:8000/
pytest -q
```

Or `make demo`, which runs all four scenarios and starts the API. The API serves the page at `/`, so one deployment (Railway: `Procfile` and `railway.json` are included) is the whole app; on first start with no `out/` it simulates the four scenarios itself.

## Keewano: live ingest, agent and /ask

```bash
python keewano.py check                      # which keys are set (never prints them); tries ingest, MCP, SQL
python keewano.py ping                       # one event round trip
python thermal.py --scenario all --push      # all four scenarios into Keewano (~90 s each)
# Keewano makes new events queryable in about 5 minutes
python keewano.py report <run_id>            # PUE / WUE / ERF as live Keewano queries
python agent.py --run <normal_run_id>        # daily agent: Keewano answers -> policy -> "agent" run, pushed
python agent.py --ask "How much water did the heat wave use?"
```

| Path | What |
| --- | --- |
| `schema.py` | The Keewano event catalog. Keewano has no float type, so values are integers in kW, kWh, gal, kg, and cents. |
| `keewano.py` | REST batch ingest (retry, batch-size backoff), SQL over the Postgres wire, MCP client, report queries. Also the scaffold `send()` / `query()` interface. |
| `agent.py` | Daily agent (`decide()` rules on three Keewano answers per day), `ask()` (Claude + Keewano MCP tools + direct SQL), `report()`. |
| `thermal_basic.py` | The first-build sim (dry-boost / precharge policy), kept with its config (`config/site_basic.yaml`), data (`data/*_basic.csv`) and tests. `agent.py --sim basic` and `POST /run?sim=basic` use it. |
| `outputs/runs/` | Runs pushed to Keewano: `index.json`, hourly CSV in Keewano units, `decisions_<agent_run>.json` (every query and answer). |

How the Keewano data is shaped: one Keewano entity per run, `uuid5("lansing:<run_id>")`. Columns are step functions, so `run_id`, `scenario`, `day`, `month`, `hour` and `decision` hold until they next change. That means `WHERE run_id = '<id>' AND month = 1` scopes every measure to January of that run.

Keewano takes about 5 minutes to make new events queryable. So the daily agent reads each day of the ingested baseline run from Keewano (three SQL queries grouped by day), applies `decide()` to day d-1 to set day d's policy, then re-runs the year as the `agent` run and pushes it. It never queries its own run hour by hour.

API additions: `GET /report?run_id=` (live from Keewano, then the last live result, then local), `POST /ask {question, run_id?}`, `GET /decisions?run_id=` (adds the full log as `detail`), `GET /runs` (adds `keewano_runs`), `POST /run?wait=false` plus `GET /jobs` for background runs, and `GET /health` (adds which env vars are set).

## Design: how should this site reuse its heat?

```bash
python design.py                 # plain-English recommendation + summary
# API: GET /design (ranked pathways, network, pit, monthly balance), GET /design/geojson (map layer)
```

`design.py` compares every way to reuse the heat on the same terms: 20-year NPV at 6%, CO2 avoided, and MW absorbed. It checks each option hour by hour against the captured heat in `data/supply.csv`.

- **District network to real buildings.** The data is `data/customers.csv`: 4,226 buildings within 15 km, with their annual heat use and what they pay for it today. It comes from the HeatOS Lansing parcel and demand pipeline. Homeowner names and addresses are removed and home locations are rounded to about 100 m. Buildings are grouped into 750 m clusters, and a network is grown outward from the site. For each cluster, the cheapest of three methods is chosen:
  - hot-water loop (heat pump at the site);
  - ambient loop (heat pump in each building);
  - direct warm loop (low-temperature buildings only).

  Every pipe is sized for everything downstream of it. Cayuga Lake is respected: west-shore buildings are reached around the south end of the lake at Ithaca, because no submarine pipe is assumed.
- **Trucked thermal storage** to large users the network doesn't reach.
- **Co-located users at the site** (greenhouse, aquaculture, drying). They use 55 C heat directly, and each is sized against the heat left every hour, up to a market cap.
- **Where the storage pit sits**: at the site, or at the load end of the trunk.

Also reported: the best network found even when it loses money, whether it pays at a $190/t carbon price, and the pipe cost at which it would break even. Every cost is in `config/design.yaml` with its source, and most are marked "assumption - verify".

## Any location: `sites.py`

Enter an address (or "lat, lon") and the IT MW on the page, or `POST /sites {address | lat+lon, it_mw}`; poll `GET /sites/{id}` (`GET /sites` lists them, Lansing first; `POST /sites/{id}/retry` resumes a failed build). CLI: `python sites.py "Ashburn, VA" --it-mw 100`.

The build (about 3-6 minutes, most of it the Keewano stream) writes `sites/<id>/` (site.yaml, supply.csv, demand.csv, customers.csv, meta.json, raw/ cache) and `web/geo/<id>.json`, then runs the normal year with `push=True`, the other scenarios locally, and `design.recommend(site=id)`. Every site-specific endpoint takes `?site=<id>` (`/runs`, `/run`, `/design`, `/design/geojson`, `/compare`, `/sensitivity`; `/ask` takes `site` in the body). Without it, everything is Lansing as before.

What is real and what is estimated: location (Nominatim), hourly 2023 temperature (Open-Meteo archive), building footprints, types, levels and water (OpenStreetMap via Overpass) are real data. Supply is Lansing's supply model scaled by IT MW / 100. Heat demand is **estimated**: footprint x levels x a heat intensity per building type, space heat scaled by the location's heating degree-days, hourly shape max(0, 16 C - T) plus 15% hot water. Prices, grid carbon and cooling constants are the Lansing defaults. `meta.json` and `site.yaml` say so line by line.

## Frontend

`/` serves `web/light.html`, the production page. It has seven tabs:

- **Site:** a light, minimal 3D map of the real Lansing site (`web/site-light.js`).
- **Report:** live Keewano PUE / WUE / ERF, the year skyline, the daily chart, and the comparison table.
- **Pipeline:** each run shown as simulate → Keewano → agent → live report, using the `BuildPipeline` component in `web/vendor/src/components/ui/`, bundled to `web/vendor/pipeline-bundle.js`.
- **Design:** the heat-reuse recommendation from `/design`.
- **Ask**, **Decisions** and **Model**.

The original dark page (`web/index.html` + `web/site.js`) is still available at `/classic`.
