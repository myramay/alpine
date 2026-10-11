import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import schema  # noqa: E402
import thermal  # noqa: E402
from schema import Policy  # noqa: E402

CFG = thermal.load_config()
_RUNS = {}


def run(scenario):
    if scenario not in _RUNS:
        _RUNS[scenario] = thermal.simulate(scenario, cfg=CFG)
    return _RUNS[scenario]


@pytest.mark.parametrize("scenario", thermal.SCENARIOS)
def test_energy_balance_every_hour(scenario):
    rows = run(scenario).hourly
    assert len(rows) == 8760
    for r in rows:
        assert r["avail_mw"] == pytest.approx(
            r["export_live_mw"] + r["store_mw"] + r["reject_dry_mw"] + r["reject_tower_mw"], abs=1e-6)
        assert min(r["store_mw"], r["reject_dry_mw"], r["reject_tower_mw"], r["unmet_mw"]) >= 0
        assert 0 <= r["soc"] <= 1


@pytest.mark.parametrize("scenario", thermal.SCENARIOS)
def test_event_stream(scenario):
    ev = run(scenario).events
    assert [e.name for e in ev[:2]] == ["run_id", "scenario"]
    assert ev[0].value == run(scenario).run_id and ev[1].value == scenario
    assert all(a.ts < b.ts for a, b in zip(ev, ev[1:]))
    assert all(e.name in schema.EVENT_ID for e in ev)
    for e in ev:
        if schema.EVENT_TYPE[e.name] == "string":
            assert isinstance(e.value, str)
        else:
            assert isinstance(e.value, int) and e.value >= 0
    assert sum(e.name == "hour" for e in ev) == 8760
    assert sum(e.name == "day" for e in ev) == 365
    assert sum(e.name == "month" for e in ev) == 12


def test_heat_wave_uses_more_water_in_window():
    w = thermal.scenario_window("heat_wave", CFG)
    water = lambda s: sum(run(s).hourly[h]["water_gal"] for h in w)  # noqa: E731
    assert water("heat_wave") > water("normal")


def test_server_outage_zero_avail():
    w = thermal.scenario_window("server_outage", CFG)
    assert len(w) == 6
    for h in w:
        r = run("server_outage").hourly[h]
        assert r["avail_mw"] == 0 and r["it_mw"] == 0


def test_dry_boost_lowers_water():
    boosted = thermal.simulate("normal", cfg=CFG, policies={0: Policy(dry_boost=0.3)})
    assert sum(r["water_gal"] for r in boosted.hourly) < sum(r["water_gal"] for r in run("normal").hourly)


def test_policy_notes_and_inheritance():
    r = thermal.simulate("normal", cfg=CFG, policies={10: Policy(dry_boost=0.2)},
                         policy_notes={10: "boost dry coolers " + "x" * 400})
    notes = [e for e in r.events if e.name == "policy_change"]
    assert len(notes) == 1 and len(notes[0].value) == 256
    assert notes[0].ts == schema.BASE_TS + 240 * 3600 + 6  # after day + 5 context events
    assert r.hourly[239]["dry_boost"] == 0 and r.hourly[5000]["dry_boost"] == 0.2
