"""Multi-site: registry, builder (network mocked), thermal/design/server with ?site=. Offline."""
import json

import numpy as np
import pandas as pd
import pytest

import design
import schema
import sites
import thermal

LAT0, LON0 = 39.0, -77.5


def _square(lat, lon, half_m):
    dlat, dlon = half_m / 110540, half_m / (111320 * np.cos(np.radians(LAT0)))
    return [{"lat": lat + a * dlat, "lon": lon + b * dlon} for a, b in ((-1, -1), (-1, 1), (1, 1), (1, -1), (-1, -1))]


def fake_overpass(query, raw=None, name="", tries=2):
    """Homes on a grid, a school, an office, a data center and a garage; a pond in the tile."""
    els, i = [], 1
    for gx in range(-30, 31, 3):
        for gy in range(-30, 31, 3):
            lat, lon = LAT0 + gy * 0.001, LON0 + gx * 0.0013
            els.append({"type": "way", "id": i, "tags": {"building": "house", "name": "Smith family"},
                        "geom": _square(lat, lon, 6)}); i += 1
    for tags, half in (({"building": "school", "name": "Oak Elementary"}, 40), ({"building": "office"}, 30),
                       ({"building": "data_center", "name": "DC1"}, 80), ({"building": "garage"}, 5),
                       ({"building": "apartments", "building:levels": "5"}, 25)):
        els.append({"type": "way", "id": i, "tags": tags, "geom": _square(LAT0 + 0.002 * i % 0.01, LON0 + 0.002, half)})
        i += 1
    if "geom;" in query:   # tile query: buildings within 1.2 km with geometry, plus water
        near = [e for e in els if abs(e["geom"][0]["lat"] - LAT0) < 0.011 and abs(e["geom"][0]["lon"] - LON0) < 0.014]
        out = [{"type": e["type"], "id": e["id"], "tags": e["tags"], "geometry": e["geom"]} for e in near]
        out.append({"type": "way", "id": 99999, "tags": {"natural": "water"}, "geometry": _square(LAT0 + 0.005, LON0, 60)})
        return {"elements": out}
    out = []
    for e in els:
        la, lo = [p["lat"] for p in e["geom"]], [p["lon"] for p in e["geom"]]
        out.append({"type": "way", "id": e["id"], "tags": e["tags"],
                    "bounds": {"minlat": min(la), "maxlat": max(la), "minlon": min(lo), "maxlon": max(lo)}})
    return {"elements": out}


def fake_weather(lat, lon, raw=None):
    h = np.arange(8760)
    return 12 - 12 * np.cos(2 * np.pi * h / 8760) + 4 * np.sin(2 * np.pi * h / 24)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("multi")
    mp = pytest.MonkeyPatch()
    mp.chdir(tmp)
    mp.setattr(sites, "SITES_DIR", tmp / "sites")
    mp.setattr(sites, "GEO_DIR", tmp / "geo")
    mp.setattr(sites, "overpass", fake_overpass)
    mp.setattr(sites, "fetch_weather", fake_weather)
    mp.setattr(sites, "geocode", lambda q, raw=None: {"lat": LAT0, "lon": LON0, "display_name": "Testville"})
    mp.setattr(sites, "_sleep", lambda s: None)
    for v in ("KEEWANO_API_KEY",):
        mp.delenv(v, raising=False)
    import keewano
    mp.setattr(keewano, "_env", lambda name: "")
    sid = sites.create_site(address="Testville, VA", it_mw=50, push=True)
    yield {"sid": sid, "tmp": tmp}
    design._cfgs_for.cache_clear()
    mp.undo()


def test_slugify_and_latlon():
    assert sites.slugify("Ashburn, VA", taken=set()) == "ashburn_va"
    assert sites.slugify("Ashburn, VA", taken={"ashburn_va"}) == "ashburn_va_2"
    assert sites.slugify("Lansing", taken=set()) == "lansing_2"
    assert sites.slugify("9 Elm St", taken=set()).startswith("site_")
    assert sites._parse_latlon("39.04, -77.49") == (39.04, -77.49)
    assert sites._parse_latlon("Ashburn") is None and sites._parse_latlon("91, 0") is None


def test_check_id_rejects_traversal():
    assert sites.check_id(None) == "lansing"
    for bad in ("../etc", "a/b", "UPPER", "nope_not_here"):
        with pytest.raises(ValueError):
            sites.check_id(bad)


def test_classify_skips_data_centers_and_never_names_homes():
    assert sites.classify({"building": "data_center"}, 5000) is None
    assert sites.classify({"building": "industrial", "name": "Equinix DC11"}, 9000) is None
    assert sites.classify({"building": "garage"}, 30) is None
    assert sites.classify({"building": "yes"}, 120) == "house"
    assert sites.classify({"building": "yes", "amenity": "school"}, 3000) == "school"
    assert sites._label({"building": "house", "name": "Smith family"}, "house") == "Residence"
    assert sites._label({"building": "school", "name": "Oak Elementary"}, "school") == "Oak Elementary"


def test_heat_shape_and_supply_scale():
    t = np.r_[np.full(4380, -5.0), np.full(4380, 25.0)]
    s = sites.heat_shape(t)
    assert s.sum() == pytest.approx(1.0) and s[0] > s[-1] > 0
    sup = sites.scaled_supply(50)
    base = pd.read_csv(sites.ROOT / "data" / "supply.csv")
    assert sup["it_mw_p50"].sum() == pytest.approx(base["it_mw_p50"].sum() / 2, rel=1e-3)
    assert (sup["cop_p50"] == base["cop_p50"]).all()


def test_aggregation_caps_rows():
    proj = sites.Proj(LAT0, LON0)
    cands = sites.parse_candidates(fake_overpass("out tags bb;"), proj)
    cust, dem, facts = sites.estimate_demand(cands, fake_weather(0, 0), proj, max_rows=50)
    assert len(cust) <= 50 and facts["buildings"] == len(cands)
    assert dem["demand_mw_p50"].sum() == pytest.approx(cust["annual_mmbtu"].sum() * 0.293071, rel=1e-3)


def test_build_writes_every_file(built):
    sid, p = built["sid"], sites.paths(built["sid"])
    assert sid == "testville_va"
    for k in ("site", "supply", "demand", "customers", "geo", "meta"):
        assert p[k].exists(), k
    meta = sites.read_meta(sid)
    assert meta["status"] == "ready" and meta["step"] == "ready" and meta["it_mw"] == 50
    assert meta["keewano"]["events"] > 8760 and meta["headline"]["first_lines"]
    assert any("ESTIMATED" in n for n in meta["notes"])
    cust = pd.read_csv(p["customers"])
    assert not cust["name"].str.contains("Smith").any()
    assert {"id", "name", "lat", "lon", "category", "nrel_type", "annual_mmbtu", "peak_kw_p50", "main_fuel"} <= set(cust)
    dem = pd.read_csv(p["demand"])
    assert len(dem) == 8760 and list(dem.columns) == ["timestamp", "outdoor_c", "demand_mw_p10", "demand_mw_p50",
                                                      "demand_mw_p90"]
    geo = json.loads(p["geo"].read_text())
    assert set(geo) == {"origin", "attribution", "buildings", "water", "land", "pipes", "candidates"}
    assert geo["buildings"] and geo["water"] and geo["candidates"]
    assert all(isinstance(v, int) for b in geo["buildings"] for pt in b["p"] for v in pt)
    assert [s["id"] for s in sites.list_sites()][:2] == ["lansing", sid]


def test_thermal_and_index_carry_site(built):
    sid = built["sid"]
    r = thermal.run(scenario="normal", site=sid, push=False, save=True, run_id="normal-12345678-beef")
    assert r["summary"]["heat_available_gwh"] == pytest.approx(
        pd.read_csv(sites.ROOT / "data" / "supply.csv")["source_heat_mw_p50"].sum() / 1000 / 2, rel=1e-3)
    evs = r["kw_run"].events
    assert [e.name for e in evs[:3]] == ["run_id", "scenario", "site"] and evs[2].value == sid
    assert r["kw_run"].site_id == sid
    assert r["kw_run"] and schema.entity_id(sid, "normal-12345678-beef") != schema.entity_id("lansing", "normal-12345678-beef")
    idx = json.loads((built["tmp"] / "outputs" / "runs" / "index.json").read_text())
    assert {e["site"] for e in idx} == {sid}
    run_json = json.loads((built["tmp"] / "out" / "run_normal-12345678-beef.json").read_text())
    assert run_json["site"] == sid
    assert thermal.CFG["site"]["id"] == "lansing"


def test_design_per_site(built):
    sid = built["sid"]
    rec = design.recommend(site=sid)
    assert rec["site_id"] == sid and rec["recommendation"][0].startswith("Reuse ")
    assert rec["summary"]["radius_km"] == 5 and rec["summary"]["demand_estimated"]
    assert design._cfgs()[0]["site"]["id"] == "lansing"         # the default is untouched afterwards
    assert design._cfgs_for(sid)[1]["network"]["lake"] == {"allow_crossing": True}
    g = design.geojson(rec, site=sid)
    site_pt = next(f for f in g["features"] if f["properties"]["kind"] == "site")
    assert site_pt["geometry"]["coordinates"] == [LON0, LAT0]


def test_server_endpoints(built, monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setattr(server, "OUT", built["tmp"] / "out")
    monkeypatch.setattr(server, "_design_cache", {})
    sid = built["sid"]
    c = TestClient(server.app)
    lst = c.get("/sites").json()["sites"]
    assert lst[0]["id"] == "lansing" and any(s["id"] == sid for s in lst)
    one = c.get(f"/sites/{sid}").json()
    assert one["status"] == "ready" and one["steps"][-1] == "ready" and one["headline"]
    assert c.get("/sites/..%2Fetc").status_code == 404
    assert c.get(f"/design?site={sid}").json()["site_id"] == sid
    assert c.get("/design?site=nope").status_code == 404
    runs = c.get(f"/runs?site={sid}").json()
    assert runs["runs"] and all(r["site"] == sid for r in runs["runs"])
    for bad in ({"address": "x", "it_mw": 0}, {"address": "x", "it_mw": 5000}, {"lat": 95, "lon": 0},
                {"lat": 10}, {"address": "x" * 300}, {}):
        assert c.post("/sites", json=bad).status_code == 422, bad
