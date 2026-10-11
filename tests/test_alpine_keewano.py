"""The glue between Alpine's sim (thermal.py) and Keewano: integer events, agent rules, report shape."""
import pytest

import agent
import keewano
import schema
import thermal


@pytest.fixture(scope="module")
def normal():
    return thermal.run(scenario="normal", push=False)


def test_kw_events_valid_and_chronological(normal):
    evs = normal["kw_run"].events
    assert evs[0].name == "run_id" and evs[1].name == "scenario"
    assert all(e.name in schema.EVENT_ID for e in evs)
    assert all(a.ts < b.ts for a, b in zip(evs, evs[1:]))
    for e in evs:
        dt = schema.EVENT_TYPE[e.name]
        if dt == "string":
            assert isinstance(e.value, str) and len(e.value) <= 256
        else:
            assert isinstance(e.value, int)
            if dt != "price_usd_cent":
                assert e.value >= 0
    assert sum(e.name == "hour" for e in evs) == 8760


def test_kw_rows_match_summary_and_balance(normal):
    rows, s = normal["kw_run"].hourly, normal["summary"]
    water = sum(r["water_gal"] for r in rows)
    assert water == pytest.approx(s["water_mgal"] * 1e6, rel=1e-9)
    pue = sum(r["power_kwh"] for r in rows) / sum(r["it_kwh"] for r in rows)
    assert pue == pytest.approx(s["pue"], rel=1e-9)
    for r in rows:
        assert r["heat_available_kw"] == pytest.approx(
            r["heat_exported_kw"] + r["heat_stored_kw"] + r["heat_rejected_kw"], abs=1e-6)


def test_what_if_runs_do_not_touch_keewano_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    import shutil, pathlib
    root = pathlib.Path(thermal.__file__).parent
    shutil.copytree(root / "data", tmp_path / "data")
    thermal.run(scenario="normal", push=False)
    assert not (tmp_path / "outputs" / "runs" / "index.json").exists()
    thermal.run(scenario="normal", push=False, save=True, run_id="normal-abcd")
    assert (tmp_path / "outputs" / "runs" / "normal-abcd.csv").exists()


def test_plan_year_alpine_uses_sabinas_rules():
    days = 5
    daily = {"source": "test", "answers": {
        "water_gal": {"question": "w", "query": "q1", "by_day": {0: 300_000, 1: 10, 2: 10, 3: 10}},
        "unmet_hours": {"question": "u", "query": "q2", "by_day": {0: 0, 1: 0, 2: 5, 3: 0}},
        "empty_hours": {"question": "e", "query": "q3", "by_day": {}},
    }}
    plan = agent.plan_year_alpine(daily, days=days)
    assert plan["policy_by_day"][1]["prefer_dry"] is True and plan["policy_by_day"][1]["tower_cap_mw"] == 20.0
    assert plan["policy_by_day"][3]["storage_reserve"] == pytest.approx(0.3)
    assert set(plan["notes"]) == {1, 3}
    assert [d["day"] for d in plan["decisions"]] == [1, 2, 3, 4]
    assert {"day", "question", "answer", "decision", "changed"} <= set(plan["decisions"][0])


def test_report_shape(monkeypatch):
    monkeypatch.setattr(keewano, "run_report", lambda run_id: {
        k: {"value": v, "query": f"SELECT {k}"} for k, v in (("pue", 1.1), ("wue", 0.4), ("erf", 0.15))})
    r = agent.report("normal-abcd")
    assert r["run_id"] == "normal-abcd" and r["cached"] is False and r["source"] == "keewano"
    assert [m["name"] for m in r["metrics"]] == ["PUE", "WUE", "ERF"]
    assert all(m["query"].startswith("SELECT") for m in r["metrics"])


def test_scaffold_send_converts_float_events(monkeypatch):
    sent = {}
    monkeypatch.setattr(keewano, "register_events", lambda: "dry_run")
    monkeypatch.setattr(keewano, "send_events", lambda site, run_id, evs, batch_size=500: (
        sent.update(evs=evs) or {"accepted": len(evs)}))
    tags = {"hour": 0, "day": 0, "month": 1, "scenario": "normal", "run_id": "normal-abcd"}
    evs = [{"name": "heat_available_mw", "value": 72.5, "tags": tags},
           {"name": "heat_stored_mw", "value": -3.0, "tags": tags},
           {"name": "storage_soc", "value": 0.5, "tags": tags},
           {"name": "decision", "value": "sell", "tags": tags}]
    n = keewano.send(evs, "normal-abcd")
    got = {e.name: e.value for e in sent["evs"]}
    assert n == len(sent["evs"])
    assert got["heat_available_kw"] == 72500 and got["heat_discharged_kw"] == 3000
    assert got["heat_stored_kw"] == 0 and got["storage_soc_pct"] == 50 and got["decision"] == "sell"


def test_run_id_formats():
    for ok in ("normal-7f3a9c", "normal-10112130-ab12", "agent-c519", "heat_wave-10112130-0f0f"):
        keewano.report_queries(ok)
