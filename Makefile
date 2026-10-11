PY ?= python3

.PHONY: setup runs demo test

setup:
	uv venv --python 3.11 && .venv/bin/pip install -r requirements.txt

runs:
	for s in normal heat_wave cold_snap server_outage; do $(PY) thermal.py --scenario $$s; done

# all four scenarios, then the API; open web/index.html (or web/index.html?api=http://localhost:8000)
demo: runs
	uvicorn server:app --port 8000

test:
	$(PY) -m pytest -q
