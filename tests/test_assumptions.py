"""Editable assumptions, per-site overrides, provenance and exports (offline, Lansing)."""
import pytest

import assumptions
import design


@pytest.fixture(autouse=True)
def tmp_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(assumptions, "OVERRIDES_DIR", tmp_path / "overrides")
    design._cfgs_for.cache_clear()
    yield tmp_path / "overrides"
    design._cfgs_for.cache_clear()


def _reg():
    return {i["key"]: i for i in assumptions.registry("lansing")}


def test_registry_defaults_sources_and_verify():
    reg = _reg()
    assert 30 <= len(reg) <= 50
    assert {i["group"] for i in reg.values()} == set(assumptions.GROUPS)
    assert reg["prices.electricity_usd_per_mwh"]["default"] == 110.0
    assert "challenge spec" in reg["prices.electricity_usd_per_mwh"]["source"]
    assert not reg["prices.electricity_usd_per_mwh"]["verify"]
    assert reg["methods.ambient.pipe_usd_per_m"]["default"] == 1100.0
    assert reg["methods.ambient.pipe_usd_per_m"]["verify"]
    assert reg["finance.discount_rate"]["source"] == "challenge spec"
    assert reg["anchors.greenhouse.max_mw"]["verify"] and "40 acres" in reg["anchors.greenhouse.max_mw"]["source"]
    assert reg["methods.trucked.max_trips_per_day"]["default"] == 24
    assert reg["fuels.gas.usd_per_mmbtu"]["source"].startswith("cost: data median")   # flow-mapping comment
    assert all(i["value"] == i["default"] and not i["overridden"] for i in reg.values())


def test_parse_comments_nesting():
    c = assumptions.parse_comments("a:\n  b: 1   # one\n  c:\n    d: 2 # two\ne: {x: 1}  # flow\n")
    assert c == {"a.b": "one", "a.c.d": "two", "e": "flow"}


def test_invalid_values_rejected():
    for bad in ({"prices.electricity_usd_per_mwh": -1}, {"finance.discount_rate": 0.9},
                {"prices.electricity_usd_per_mwh": "abc"}, {"prices.electricity_usd_per_mwh": True},
                {"nope.key": 1}, {"prices.electricity_usd_per_mwh": float("nan")}, {}):
        with pytest.raises(ValueError):
            assumptions.save("lansing", bad)
    assert assumptions.load_overrides("lansing") == {}


def test_override_save_apply_and_reset():
    ov = assumptions.save("lansing", {"methods.trucked.max_trips_per_day": "30", "finance.discount_rate": 0.08})
    assert ov == {"methods.trucked.max_trips_per_day": 30, "finance.discount_rate": 0.08}
    assert design._cfgs()[1]["finance"]["discount_rate"] == 0.08
    reg = _reg()
    assert reg["finance.discount_rate"]["overridden"] and reg["finance.discount_rate"]["value"] == 0.08
    assumptions.save("lansing", {"finance.discount_rate": None})           # one key back to default
    assert assumptions.load_overrides("lansing") == {"methods.trucked.max_trips_per_day": 30}
    assumptions.reset("lansing")
    assert assumptions.load_overrides("lansing") == {}
    assert design._cfgs()[1]["finance"]["discount_rate"] == 0.06
    hist = assumptions.grouped("lansing")["history"]
    assert hist[-1]["key"] == "*" and any(h["key"] == "finance.discount_rate" for h in hist)


def test_thermal_sees_site_overrides():
    import thermal
    assumptions.save("lansing", {"storage.capacity_mwh": 1000})
    assert thermal.site_cfg("lansing")["storage"]["capacity_mwh"] == 1000
    assert thermal.CFG["storage"]["capacity_mwh"] == 2800.0                # the module config is untouched


def test_overrides_move_design_in_the_expected_direction():
    c = design.cluster(design.load_buildings())
    base = design.design_network(c)["best_candidate"]["npv_usd"]
    base_rec = design.recommend()
    gh = next(p for p in base_rec["pathways"] if p["key"] == "greenhouse")

    assumptions.save("lansing", {f"methods.{m}.pipe_usd_per_m": 2 * _reg()[f"methods.{m}.pipe_usd_per_m"]["default"]
                                 for m in ("hot_water", "ambient", "direct")})
    worse = design.design_network(design.cluster(design.load_buildings()))["best_candidate"]["npv_usd"]
    assert worse <= base                                                    # pricier pipe never helps the network

    assumptions.reset("lansing")
    assumptions.save("lansing", {"anchors.heat_price_usd_per_mwh": 20})
    rec = design.recommend()
    gh2 = next(p for p in rec["pathways"] if p["key"] == "greenhouse")
    assert gh2["npv_usd"] < gh["npv_usd"]                                   # cheaper heat, lower anchor NPV
    pv = rec["provenance"]["pathways"]["greenhouse"]["inputs"]
    hp = next(i for i in pv if i["key"] == "anchors.heat_price_usd_per_mwh")
    assert hp["overridden"] and hp["value"] == 20 and hp["default"] == 30.0 and hp["verify"]

    assumptions.reset("lansing")
    assumptions.save("lansing", {"data_center.it_load_mw": 50})
    half = design.recommend()
    assert half["summary"]["heat_available_gwh"] == pytest.approx(base_rec["summary"]["heat_available_gwh"] / 2, rel=0.01)
    assert half["location"]["it_mw"] == 50


def test_provenance_shape():
    rec = design.recommend()
    pv = rec["provenance"]
    assert {"heat_available_gwh", "share_reused", "npv_recommended_usd", "co2_avoided_t"} <= set(pv["summary"])
    assert {p["key"] for p in rec["pathways"]} <= set(pv["pathways"])
    ins = pv["summary"]["heat_available_gwh"]["inputs"]
    assert ins[0]["key"] == "data_center.it_load_mw" and not ins[0]["overridden"]
    assert any("supply model" in d.lower() for d in pv["summary"]["heat_available_gwh"]["data"])
    assert pv["data_sources"]["supply"]


def test_endpoints(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setattr(server, "_design_cache", {})
    c = TestClient(server.app)
    g = c.get("/sites/lansing/assumptions").json()
    assert [x["group"] for x in g["groups"]] == assumptions.GROUPS and g["overridden"] == 0

    r = c.put("/sites/lansing/assumptions", json={"finance.discount_rate": 2})
    assert r.status_code == 422 and "outside" in r.json()["detail"]
    r = c.put("/sites/lansing/assumptions", json={"anchors.heat_price_usd_per_mwh": 20})
    assert r.status_code == 200
    j = r.json()
    assert j["after"]["npv_recommended_usd"] < j["before"]["npv_recommended_usd"]
    assert any(x["what"] == "greenhouse.npv_usd" for x in j["changed"])
    assert j["overrides"] == {"anchors.heat_price_usd_per_mwh": 20}
    d = c.get("/design").json()
    assert d["provenance"]["overrides"] == {"anchors.heat_price_usd_per_mwh": 20}

    h = c.get("/sites/lansing/plan.html")
    assert h.status_code == 200 and h.headers["content-type"].startswith("text/html")
    assert 'filename="alpine-plan-lansing-' in h.headers["content-disposition"]
    for text in ("Executive summary", "Monthly heat balance", "Assumptions", "Methodology", "overridden", "verify"):
        assert text in h.text
    pj = c.get("/sites/lansing/plan.json")
    assert pj.status_code == 200 and "attachment" in pj.headers["content-disposition"]
    body = pj.json()
    assert {"design", "assumptions", "provenance", "generator"} <= set(body)
    pc = c.get("/sites/lansing/pathways.csv")
    assert pc.status_code == 200 and pc.text.startswith("key,label,verdict") and "greenhouse" in pc.text
    assert "anchors.heat_price_usd_per_mwh=20" in pc.text
    ac = c.get("/sites/lansing/assumptions.csv")
    assert ac.status_code == 200 and "prices.electricity_usd_per_mwh" in ac.text
    assert 'filename="alpine-assumptions-lansing-' in ac.headers["content-disposition"]

    r = c.delete("/sites/lansing/assumptions")
    assert r.status_code == 200 and r.json()["overrides"] == {}
    assert r.json()["after"]["npv_recommended_usd"] == j["before"]["npv_recommended_usd"]
    assert c.get("/sites/nope/assumptions").status_code == 404
    assert c.get("/sites/nope/plan.html").status_code == 404
