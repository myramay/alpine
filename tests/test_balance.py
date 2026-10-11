import pytest

import thermal


def test_cop_is_sane():
    assert 2.0 < thermal.cop(55, 70, 0.6) < 20.0
    assert thermal.cop(55, 70, 0.6) > thermal.cop(55, 90, 0.6)


def test_inputs_load_full_year():
    df = thermal.load_inputs()
    assert len(df) == 8760 and {"source_heat_mw_p50", "demand_mw_p50"} <= set(df.columns)


@pytest.fixture(scope="module")
def normal_run():
    return thermal.run(scenario="normal", push=False, save=False)


def test_energy_balance(normal_run):
    """available == exported + stored_in + rejected every hour (discharge is negative stored, so add it back)."""
    by_hour = {}
    for e in normal_run["events"]:
        by_hour.setdefault(e["tags"]["hour"], {})[e["name"]] = e["value"]
    assert len(by_hour) == 8760
    for h, m in by_hour.items():
        stored_in = max(m["heat_stored_mw"], 0.0)
        assert abs(m["heat_available_mw"] - (m["heat_exported_mw"] + stored_in + m["heat_rejected_mw"])) < 1e-3, h


def test_summary_is_sane(normal_run):
    s = normal_run["summary"]
    assert 1.0 < s["pue"] < 1.5
    assert 0.0 <= s["share_reused"] <= 1.0
    assert s["hours_tower"] + s["hours_pit_empty"] >= 0
    assert normal_run["n_events"] == 8760 * len(thermal.EVENT_NAMES)


def test_server_outage_zeroes_supply():
    r = thermal.run(scenario="server_outage", push=False, save=False)
    sc = thermal.CFG["scenarios"]["server_outage"]
    hrs = {e["tags"]["hour"] for e in r["events"] if e["name"] == "heat_available_mw" and e["value"] == 0}
    assert set(range(sc["start_hour"], sc["start_hour"] + sc["hours"])) <= hrs
