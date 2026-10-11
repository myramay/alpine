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
