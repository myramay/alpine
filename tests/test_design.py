"""Design stage: heat-reuse recommendation over the real Lansing buildings."""
import numpy as np
import pytest

import design


@pytest.fixture(scope="module")
def rec():
    return design.recommend()


@pytest.fixture(scope="module")
def clusters():
    return design.cluster(design.load_buildings())


def test_recommendation_shape(rec):
    assert {"summary", "recommendation", "pathways", "network", "pit", "anchors", "trucked"} <= set(rec)
    assert rec["summary"]["buildings_within_15km"] == 4226
    npvs = [p["npv_usd"] for p in rec["pathways"]]
    assert npvs == sorted(npvs, reverse=True)
    assert all(p["verdict"] in {"recommend", "only with a carbon price", "not recommended"} for p in rec["pathways"])
    assert rec["recommendation"][0].startswith("Reuse ")


def test_reuse_never_exceeds_captured_heat(rec):
    s = rec["summary"]
    assert 0 < s["heat_reused_gwh"] <= s["heat_available_gwh"]
    for m in rec["hourly"]["months"]:
        taken = sum(v for k, v in m.items() if k not in ("month", "avail"))
        assert taken <= m["avail"] + 1e-6


def test_anchors_respect_caps(rec):
    caps = design._cfgs()[1]["anchors"]
    for a in rec["anchors"]:
        assert 0 <= a["peak_mw"] <= caps[a["key"]]["max_mw"]


def test_lake_routing():
    site = design._cfgs()[0]["site"]
    assert not design._west(site["lat"], site["lon"])
    assert design._west(42.5368, -76.6535)          # Trumansburg Central School, west shore
    assert not design._west(42.5448, -76.5319)      # Lansing Central School, east shore
    straight = design._km(site["lat"], site["lon"], 42.5368, -76.6535)
    routed = float(design._route_km(site["lat"], site["lon"], 42.5368, -76.6535))
    assert routed > 2 * straight


def test_cheaper_pipe_grows_the_network(clusters):
    small = design.design_network(clusters, pipe_mult=0.5)
    big = design.design_network(clusters, pipe_mult=0.2)
    assert big["buildings"] >= small["buildings"]
    for n in (small, big):
        if n["connected"]:
            assert n["npv_usd"] > 0 and n["pipe_km"] > 0


def test_carbon_price_never_hurts(clusters):
    base = design.design_network(clusters, pipe_mult=0.3)
    carb = design.design_network(clusters, pipe_mult=0.3, carbon=True)
    assert carb["npv_usd"] >= base["npv_usd"] - 1


def test_geojson(rec):
    g = design.geojson(rec)
    kinds = {f["properties"]["kind"] for f in g["features"]}
    assert {"site", "cluster"} <= kinds
    assert all(len(f["geometry"]["coordinates"]) == 2 for f in g["features"] if f["properties"]["kind"] == "site")


def test_linear_heat_density_is_low(clusters):
    lhd = design.linear_heat_density(clusters)
    assert 0 < lhd < design._cfgs()[1]["network"]["viable_mwh_per_m"]
    assert np.isfinite(lhd)
