import pytest

import thermal


def test_cop_is_sane():
    assert 2.0 < thermal.cop(55, 70, 0.6) < 20.0
    assert thermal.cop(55, 70, 0.6) > thermal.cop(55, 90, 0.6)


def test_inputs_load_full_year():
    df = thermal.load_inputs()
    assert len(df) == 8760 and {"source_heat_mw_p50", "demand_mw_p50"} <= set(df.columns)


@pytest.mark.skipif(True, reason="enable once thermal.run is implemented")
def test_energy_balance():
    r = thermal.run(scenario="normal", push=False)
    ev = r["events"]
    by_hour = {}
    for e in ev:
        by_hour.setdefault(e["tags"]["hour"], {})[e["name"]] = e["value"]
    for h, m in by_hour.items():
        assert abs(m["heat_available_mw"] - (m["heat_exported_mw"] + m["heat_stored_mw"] + m["heat_rejected_mw"])) < 1e-6, h
