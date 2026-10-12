# Alpine

**The thermal layer for data centers.** Every hour, Alpine decides where a data center's heat goes: sold to nearby buildings, stored in a hot-water pit, or thrown away by dry coolers (no water) or cooling towers (which evaporate water). Every hour streams into Keewano as an event, so PUE, water use and heat reuse are live queries you can check, and anyone can ask the site a question in plain English.

Live demo: https://alpine-production-44bf.up.railway.app

Built by Sabina Guliyeva and Myra at SpeedHacks, October 11, 2026.

## The problem

A data center turns almost all of its electricity into heat. Today most of that heat is thrown away, often by cooling towers that evaporate millions of gallons of water, while the buildings next door burn gas to stay warm.

Reporting on it is slow too. Every quarter, an operations or sustainability team exports hourly logs from the cooling and power systems, cleans them in spreadsheets, builds the PUE, WUE and heat-reuse formulas, sends them for review, and starts over when a number looks wrong. Nobody can trace where a number came from.

## The time we save

| Task | Today | With Alpine |
| --- | --- | --- |
| Quarterly PUE, WUE and heat-reuse report | Weeks of exporting and spreadsheet work (our estimate) | A live page; review takes minutes because the query sits under each number |
| A one-off question ("how much water did the heat wave cost?") | A request to an analyst, often days | About 10 seconds in Ask, with the query shown |
| Fixing a wrong number | Rebuild the spreadsheet | Re-run one query |
| Deciding when to save water vs. power | Guesswork | A daily agent that decides from yesterday's data and logs why |

The one-time setup is connecting a site's data.

## What it does

We modeled a real proposed site: a 100 MW data center at a former coal plant on Cayuga Lake in Lansing, New York.

- **Site:** a 3D map of the real town (1,363 OpenStreetMap buildings, the Census shoreline, 88 candidate heat customers). Press Play to watch a year: pipes light up when buildings buy heat, and the tower steam grows with water use.
- **Report:** PUE, WUE and ERF computed live from Keewano, with the exact SQL under each number. Also a 3D skyline of the year, a day-by-day chart of where the heat went, and towers vs. dry coolers side by side.
- **Ask:** type a question in plain English. Claude writes the SQL, runs it on Keewano, and answers with the query shown.
- **Decisions:** the daily agent's log. Every day it asks Keewano three questions about yesterday (water used, unmet heat demand, pit empty) and sets today's cooling policy.
- **Design:** which heat-reuse options actually pay at this site, compared on 20-year NPV, CO2 avoided and heat absorbed.
- **Pipeline:** one run end to end: simulate → Keewano → agent → live report.
- **Model:** pushes each assumption 25% up and down and shows how much the results move.

## Results for the Lansing site

- **Baseline year:** PUE 1.10, 102 million gallons of water, 15% of energy reused as heat. Germany requires 10% for new data centers.
- **After the agent switches cooling to dry coolers:** 98% less water (1.5 million gallons) for about 1.6% more on the electricity bill.
- **Best use of the heat here:** a network to existing homes doesn't pay, because Lansing is mostly single-family homes. The best network found loses $5.3M. Co-locating a greenhouse, a fish farm and crop drying at the site, plus trucked thermal storage, reuses 29% of the heat for a 20-year NPV of $42M.

## How we use Keewano

Keewano is where every number lives.

- **Ingest.** Every simulated hour is one event: heat captured, sold, stored, and rejected to dry coolers and towers, plus water, power, cost, CO2, pit level and the decision. Each run is its own Keewano entity, so `WHERE run_id = '…' AND month = 1` scopes any measure to one month of one run. Keewano has no float type, so values are stored as integers (kW, kWh, gallons, kg, cents; see `schema.py`).
- **SQL.** The Report tab's PUE, WUE and ERF are queries over Keewano's Postgres interface, shown on the page.
- **Agent.** The daily agent decides from three Keewano queries grouped by day. Keewano takes about 5 minutes to make new events queryable, so the agent replays a baseline year that's already ingested, then re-simulates the year with its policy and pushes that run back.
- **MCP.** Ask gives Claude every tool on Keewano's MCP server plus a direct SQL tool.

## How it works

- **Simulator (`thermal.py`).** Steps through all 8,760 hours. Each hour it captures about 75% of the server heat in a liquid loop, sells to the network up to demand (heat pumps at COP ≈ 7), charges or draws the 2,800 MWh pit, then rejects the rest with dry coolers and then towers. The chiller heat goes to towers or dry condensers, and that choice is the agent's lever. Every constant is in `config/site.yaml` with its source.
- **Data.** `data/supply.csv` comes from a compute-load model trained on 7 public trace sources (IT load and heat at P10/P50/P90). `data/demand.csv` comes from a quantile demand model trained on NYC LL84 building data and NREL ComStock/ResStock, run on NOAA Ithaca weather. Validation is in `data/MODEL_VALIDATION.txt`.
- **Agent (`agent.py`).** Rules over Keewano answers, on purpose, so an operator can audit every decision. Every query and answer is saved with the decision.
- **Ask (`agent.py`).** A Claude tool-use loop of up to 6 steps. It writes SQL, reads errors, fixes them, and returns the answer, the query and the rows.
- **Design (`design.py`).** 4,226 buildings within 15 km are grouped into 750 m clusters. A network is grown outward from the site, choosing the cheapest of three loop types per cluster, and it goes around the lake, never under it. Co-located users and trucked storage are sized hour by hour. Costs are in `config/design.yaml` with sources.
- **API and UI.** One FastAPI app (`server.py`) serves the page and the API. The UI is plain HTML with three.js and two small React components, bundled with esbuild.

## Run it

```bash
uv venv --python 3.11 && source .venv/bin/activate && uv pip install -r requirements.txt
cp .env.example .env          # Keewano and Anthropic keys
make demo                     # simulates four scenarios, starts the API
# open http://localhost:8000
```

With Keewano:

```bash
python keewano.py check                      # which keys are set; tries ingest, MCP, SQL
python thermal.py --scenario all --push      # four scenarios into Keewano
python keewano.py report <run_id>            # PUE / WUE / ERF as live Keewano queries
python agent.py --run <normal_run_id>        # daily agent
python agent.py --ask "How much water did the heat wave use?"
pytest -q
```

Deployed on Railway (`Procfile`, `railway.json`). On first start with no data, the app simulates all four scenarios itself.

## Repo map

| Path | What |
| --- | --- |
| `thermal.py` | Hourly simulator and cooling policy |
| `schema.py`, `keewano.py` | Keewano event schema, ingest, SQL and MCP client |
| `agent.py` | Daily agent and Ask |
| `design.py` | Heat-reuse design and NPV comparison |
| `server.py` | FastAPI: page and API |
| `web/light.html` | The app (the original dark page is at `/classic`) |
| `config/` | Every constant, with sources |
| `data/` | Model outputs and validation |
| `questions.md` | Questions a judge might ask, with the SQL |
| `docs/EVENTS.md` | The event schema |
| `sites.py` | Plan any location: geocode, weather, OSM buildings, estimated demand, sim, Keewano ingest, design |
| `web/site-light.js` | The isometric 3D site plan |
| `thermal_basic.py` | The first-build sim, kept with its config, data and tests |

## Technical details

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
