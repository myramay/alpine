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

The hourly supply and demand curves are outputs of trained models from the HeatOS project (`myramay/data-center-heatos`): a multi-source compute-load trace model (7 public trace sources, 134 domains) for supply, and a quantile demand model trained on NYC LL84 and NREL profiles for demand, run for the Lansing, NY site on NOAA Ithaca weather. `data/MODEL_VALIDATION.txt` is their held-out validation. We use the outputs as CSVs; nothing else from that project is used.

- `data/supply.csv`: phase 1, AI inference, 100 MW IT. Columns: IT MW, source heat MW, delivered heat MW, heat-pump electricity MW, COP, each at P10/P50/P90.
- `data/demand.csv`: heat demand from buildings within 15 km, P10/P50/P90, plus outdoor temperature.

## Run

```
uv venv --python 3.11 && source .venv/bin/activate && uv pip install -r requirements.txt
cp .env.example .env   # fill in keys
python thermal.py --scenario normal --push
python agent.py --run <run_id>
uvicorn server:app --port 8000
# open web/index.html (set API_BASE at the top if the API is not on localhost:8000)
pytest -q
```
