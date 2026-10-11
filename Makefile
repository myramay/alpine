PY ?= python3

.PHONY: setup runs demo test ingest agent

setup:
	uv venv --python 3.11 && .venv/bin/pip install -r requirements.txt

runs:
	for s in normal heat_wave cold_snap server_outage; do $(PY) thermal.py --scenario $$s; done

# all four scenarios, then the API; open web/index.html (or web/index.html?api=http://localhost:8000)
demo: runs
	$(PY) -m uvicorn server:app --port 8000

test:
	$(PY) -m pytest -q

# Keewano: ingest all four scenarios, then the daily agent on the latest normal run
ingest:
	$(PY) thermal.py --scenario all --push

agent:
	$(PY) agent.py
