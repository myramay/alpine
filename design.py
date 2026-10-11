"""Design stage: recommend how this site should reuse its heat.

    recommend() -> {"summary", "recommendation", "pathways", "network", "pit", "hourly", "assumptions"}

Three kinds of reuse are compared on the same terms (20-year NPV at 6%, CO2 avoided, MW absorbed):

  1. A district network to the real buildings around the site (data/customers.csv: 4,226 buildings
     with coordinates, annual heat and what they pay for it today). Buildings are grouped into ~750 m
     clusters; a network is grown outward from the site, adding the cluster whose connection pays
     best given the pipe to the nearest point already on the network. For each cluster the cheapest
     of three methods is chosen: hot-water loop (heat pump at the site), ambient loop (heat pump in
     each building) or direct warm loop (low-temperature buildings only). Because the nearest dense
     load is kilometres away, no single cluster pays for the trunk alone, so the search is seeded:
     each of the largest clusters is tried as the far end of the first trunk, and the best total wins.
  2. Trucked thermal storage to large users the network does not reach.
  3. New anchor users co-located at the site (greenhouse, aquaculture, drying) that take 55 C heat
     with no long pipe; sized against the heat that is left every hour, up to a market cap.

Then the hourly check: network + anchors against data/supply.csv's captured heat, hour by hour, and
where the storage pit should sit (at the site or at the load end of the trunk).
Every cost is in config/design.yaml with its source; most are "assumption - verify".
"""
from __future__ import annotations

import contextlib
import contextvars
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
MMBTU_MWH = 0.293071  # 1 MMBtu = 0.293071 MWh


# The site every helper works on. recommend()/geojson()/load_* take site= and set it for their duration,
# so the inner helpers (cluster, _grow, ...) keep their signatures. Default: Lansing.
_SITE: contextvars.ContextVar[str] = contextvars.ContextVar("design_site", default="lansing")


@lru_cache(maxsize=16)
def _cfgs_for(site: str) -> tuple[dict, dict]:
    import sites
    p = sites.paths(sites.check_id(site))
    cfg = yaml.safe_load(p["site"].read_text())
    design = yaml.safe_load((ROOT / "config" / "design.yaml").read_text())
    if site != "lansing":   # the Cayuga Lake routing block is Lansing's only
        design["network"]["lake"] = {"allow_crossing": True}
    return cfg, design


def _cfgs() -> tuple[dict, dict]:
    return _cfgs_for(_SITE.get())


@contextlib.contextmanager
def using(site: str | None):
    """Run the block against `site` (None: keep the current one)."""
    if site is None:
        yield
        return
    tok = _SITE.set(site)
    try:
        yield
    finally:
        _SITE.reset(tok)


def _paths() -> dict:
    import sites
    return sites.paths(_SITE.get())


def _radius_km() -> float:
    return float(_cfgs()[0].get("design_radius_km", 15))


def _pvf(r: float, n: int) -> float:
    return (1 - (1 + r) ** -n) / r


def _km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (numpy-friendly)."""
    p = np.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * np.arcsin(np.sqrt(a))


def _west(lat, lon):
    """True for points on the far (west) shore of Cayuga Lake from the site."""
    _, d = _cfgs()
    if "shore" not in d["network"].get("lake", {}):
        return np.zeros(np.shape(lat), bool) if np.ndim(lat) else np.False_
    (la1, lo1), (la2, lo2) = d["network"]["lake"]["shore"]
    line = lo1 + (np.asarray(lat) - la1) / (la2 - la1) * (lo2 - lo1)
    return np.asarray(lon) < line


def _route_km(lat1, lon1, lat2, lon2):
    """Straight-line km, except across the lake: around its south end (no submarine pipe)."""
    _, d = _cfgs()
    straight = _km(lat1, lon1, lat2, lon2)
    lake = d["network"]["lake"]
    if lake.get("allow_crossing"):
        return straight
    ta, to = lake["around"]
    cross = _west(lat1, lon1) != _west(lat2, lon2)
    around = _km(lat1, lon1, ta, to) + _km(ta, to, lat2, lon2)
    return np.where(cross, around, straight)


def _carnot_cop(t_src: float, t_sink: float, frac: float, cap: float) -> float:
    tk = t_sink + 273.15
    return min(cap, max(1.0, frac * tk / max(t_sink - t_src, 1.0)))


def _size_factor(peak_mw):
    """Pipe cost scales with capacity: 1.0 at 5 MW peak, ^0.4, clipped."""
    return np.clip((np.asarray(peak_mw, float) / 5.0) ** 0.4, 0.4, 2.0)


# ------------------------------------------------------------------------------------- inputs

def load_buildings(site: str | None = None) -> pd.DataFrame:
    with using(site):
        return _load_buildings()


def _load_buildings() -> pd.DataFrame:
    site, d = _cfgs()
    b = pd.read_csv(_paths()["customers"])
    fuels = d["fuels"]
    b["fuel"] = b["main_fuel"].fillna("unknown").where(b["main_fuel"].isin(list(fuels)), "unknown")
    default_cost = b["fuel"].map({k: v["usd_per_mmbtu"] for k, v in fuels.items()})
    b["cost_usd_mmbtu"] = b["current_cost_X_usd_per_mmbtu"].fillna(default_cost)
    b["mwh"] = b["annual_mmbtu"] * MMBTU_MWH                       # heat delivered to the building per year
    b["value_usd_mwh"] = b["cost_usd_mmbtu"] / MMBTU_MWH * d["network"]["tariff_share_of_current_cost"]
    b["co2_t_mwh"] = b["fuel"].map({k: v["t_co2_per_mwh"] / v["boiler_eff"] for k, v in fuels.items()})
    b["peak_mw"] = b["peak_kw_p50"] / 1000.0
    b["direct_ok"] = b["nrel_type"].isin(d["methods"]["direct"]["eligible_types"])
    return b


def load_hourly(site: str | None = None) -> pd.DataFrame:
    """Captured heat (MW) and outdoor temperature per hour, plus the buildings' normalized demand shape."""
    with using(site):
        p = _paths()
    s = pd.read_csv(p["supply"])
    dm = pd.read_csv(p["demand"])
    shape = dm["demand_mw_p50"].to_numpy()
    return pd.DataFrame({"avail_mw": s["source_heat_mw_p50"].to_numpy(), "outdoor_c": dm["outdoor_c"].to_numpy(),
                         "bldg_shape": shape / shape.sum()})


def cluster(b: pd.DataFrame) -> pd.DataFrame:
    site, d = _cfgs()
    lat0, lon0 = site["site"]["lat"], site["site"]["lon"]
    cell = d["network"]["cluster_m"] / 1000.0
    y = (b["lat"] - lat0) * 111.0
    x = (b["lon"] - lon0) * 111.0 * math.cos(math.radians(lat0))
    b = b.assign(cx=np.floor(x / cell).astype(int), cy=np.floor(y / cell).astype(int))
    b["w_val"] = b["mwh"] * b["value_usd_mwh"]
    b["w_co2"] = b["mwh"] * b["co2_t_mwh"]
    b["mwh_direct"] = b["mwh"] * b["direct_ok"]
    # rows can be blocks of homes (sites.py aggregates small homes): count buildings, not rows
    b["nb"] = b["n_buildings"].fillna(1).astype(int) if "n_buildings" in b else 1
    b["nb_home"] = b["nb"] * b["category"].isin(["Residential", "Farm residence"])
    g = b.groupby(["cx", "cy"])
    c = pd.DataFrame({
        "n": g["nb"].sum(), "mwh": g["mwh"].sum(), "peak_mw": g["peak_mw"].sum(),
        "value_usd_mwh": g["w_val"].sum() / g["mwh"].sum(), "co2_t_mwh": g["w_co2"].sum() / g["mwh"].sum(),
        "direct_share": g["mwh_direct"].sum() / g["mwh"].sum(),
        "lat": g["lat"].mean(), "lon": g["lon"].mean(),
        "schools": g["nrel_type"].apply(lambda s: int((s == "primaryschool").sum())),
        "homes": g["nb_home"].sum().astype(int),
        "top": g.apply(lambda df: str(df.loc[df["mwh"].idxmax(), "name"]), include_groups=False),
    }).reset_index()
    c["site_km"] = _route_km(lat0, lon0, c["lat"].to_numpy(), c["lon"].to_numpy())
    c["west_shore"] = _west(c["lat"].to_numpy(), c["lon"].to_numpy())
    return c


# --------------------------------------------------------------------------- method economics

def _method_terms(c: pd.DataFrame, carbon: bool = False, pipe_mult: float = 1.0) -> dict[str, dict[str, np.ndarray]]:
    """Per cluster and method: NPV(L) = a - b * L, L = branch pipe metres. Plus the quantities behind it.
    carbon=True adds avoided CO2 at the carbon price to the annual value; pipe_mult scales every pipe cost."""
    site, d = _cfgs()
    f, m, hp = d["finance"], d["methods"], d["heat_pump"]
    pvf = _pvf(f["discount_rate"], f["horizon_years"])
    om = f["om_share_of_capex"]
    elec = site["prices"]["electricity_usd_per_mwh"]
    grid = site["emissions"]["grid_t_per_mwh"]
    pump = d["network"]["pump_kwh_per_mwh"] / 1000.0
    detour = d["network"]["detour_factor"]
    E, peak, val, co2f = (c[k].to_numpy() for k in ("mwh", "peak_mw", "value_usd_mwh", "co2_t_mwh"))
    path_km = c["site_km"].to_numpy() * detour
    sf = _size_factor(peak)
    src_t = site["data_center"]["supply_temp_c"]
    out = {}
    street_m = c["n"].to_numpy() * d["network"]["street_m_per_building"]
    for key in ("hot_water", "ambient", "direct"):
        mm = m[key]
        keep = np.clip(1 - mm["loss_per_km"] * path_km, 0.5, 1.0)
        if mm["hp_at"] == "site":
            hp_out = E / keep
            elec_mwh = hp_out / hp["site_cop"]
            src = hp_out - elec_mwh                       # captured heat drawn from the data center
            capex_fixed = mm["station_usd_per_kw"] * peak * 1000 + mm["hp_usd_per_kw"] * peak / keep * 1000
        elif mm["hp_at"] == "building":
            cop_b = _carnot_cop(mm["supply_temp_c"], mm["sink_temp_c"], hp["carnot_fraction"], hp["cop_cap"])
            elec_mwh = E / cop_b
            src = (E - elec_mwh) / keep
            capex_fixed = mm["hp_usd_per_kw"] * peak * 1000
        else:
            elec_mwh = np.zeros_like(E)
            src = E / keep
            capex_fixed = mm["station_usd_per_kw"] * peak * 1000
        elec_mwh = elec_mwh + E * pump
        co2 = E * co2f - elec_mwh * grid
        annual = E * val - elec_mwh * elec + (co2 * f["carbon_price_usd_per_t"] if carbon else 0.0)
        pipe_per_m = mm["pipe_usd_per_m"] * sf * pipe_mult
        capex_fixed = capex_fixed + street_m * mm["pipe_usd_per_m"] * d["network"]["street_cost_share"] * pipe_mult
        a = annual * pvf - capex_fixed * (1 + om * pvf)
        b = pipe_per_m * (1 + om * pvf)
        if key == "direct":
            a = np.where(c["direct_share"].to_numpy() >= 0.8, a, -np.inf)   # only low-temperature clusters
        out[key] = {"a": a, "b": b, "annual": annual, "capex_fixed": capex_fixed, "pipe_per_m": pipe_per_m,
                    "co2": co2, "src": src, "elec": elec_mwh}
    return out


def _grow(c: pd.DataFrame, terms: dict, seed: int | None) -> tuple[list[dict], float]:
    """Greedy network from the site. seed: a cluster whose trunk is laid first even if it loses money."""
    site, d = _cfgs()
    detour = d["network"]["detour_factor"]
    lat0, lon0 = site["site"]["lat"], site["site"]["lon"]
    lat, lon = c["lat"].to_numpy(), c["lon"].to_numpy()
    n = len(c)
    keys = list(terms)
    A = np.stack([terms[k]["a"] for k in keys])           # methods x clusters
    B = np.stack([terms[k]["b"] for k in keys])
    near_km = _route_km(lat0, lon0, lat, lon) * detour      # pipe to the nearest point on the network
    parent = np.full(n, -1)
    done = np.zeros(n, bool)
    edges, total = [], 0.0

    def connect(i: int):
        nonlocal total
        L = near_km[i] * 1000
        npv = A[:, i] - B[:, i] * L
        k = int(np.argmax(npv))
        edges.append({"cluster": i, "parent": int(parent[i]), "method": keys[k], "pipe_m": float(L),
                      "npv": float(npv[k])})
        total += float(npv[k])
        done[i] = True
        dk = _route_km(lat[i], lon[i], lat, lon) * detour
        closer = dk < near_km
        parent[closer] = i
        near_km[:] = np.minimum(near_km, dk)

    if seed is not None:
        connect(seed)
    while True:
        npv = (A - B * (near_km * 1000)).max(axis=0)
        npv[done] = -np.inf
        i = int(np.argmax(npv))
        if not np.isfinite(npv[i]) or npv[i] <= 0:
            break
        connect(i)
    return edges, total


def _best_prefix(c: pd.DataFrame, terms: dict, edges: list[dict], pipe_mult: float) -> tuple[int, float]:
    """Exact NPV of every prefix of the tree (each pipe sized for what is downstream); best (length, npv).
    Edges are in the order they were added, so every prefix is itself a connected tree."""
    _, d = _cfgs()
    f = d["finance"]
    pvf = _pvf(f["discount_rate"], f["horizon_years"])
    om = f["om_share_of_capex"]
    peak = c["peak_mw"].to_numpy()
    pos = {e["cluster"]: j for j, e in enumerate(edges)}
    par = [pos.get(e["parent"], -1) for e in edges]
    base = [terms[e["method"]]["annual"][e["cluster"]] * pvf
            - terms[e["method"]]["capex_fixed"][e["cluster"]] * (1 + om * pvf) for e in edges]
    rate = [d["methods"][e["method"]]["pipe_usd_per_m"] * pipe_mult * e["pipe_m"] * (1 + om * pvf) for e in edges]
    down = np.zeros(len(edges))
    best_k, best_v, fixed = 0, 0.0, 0.0
    for k, e in enumerate(edges):              # add edge k: its load flows through every ancestor
        down[k] = peak[e["cluster"]]
        j = par[k]
        while j >= 0:
            down[j] += peak[e["cluster"]]
            j = par[j]
        fixed += base[k]
        v = fixed - float(np.dot(rate[: k + 1], _size_factor(down[: k + 1])))
        if v > best_v:
            best_k, best_v = k + 1, v
    return best_k, best_v


def _best_tree(c: pd.DataFrame, terms: dict, pipe_mult: float = 1.0) -> tuple[list[dict], float, int | None]:
    """Best seeded tree after exact pruning. If nothing pays, the best full candidate (to say by how much)."""
    _, d = _cfgs()
    best, fallback = ([], 0.0, None), ([], -np.inf, None)
    seeds = c["mwh"].sort_values(ascending=False).index[: d["network"]["max_seeds"]]
    for s in [None, *map(int, seeds)]:
        edges, total = _grow(c, terms, s)
        if not edges:
            continue
        k, v = _best_prefix(c, terms, edges, pipe_mult)
        if k and v > best[1]:
            best = (edges[:k], v, s)
        if total > fallback[1]:
            fallback = (edges, total, s)
    return best if best[0] else fallback


def design_network(c: pd.DataFrame | None = None, carbon: bool = False, pipe_mult: float = 1.0) -> dict:
    """The network to build (empty if none pays), plus the best candidate tree and what it would take."""
    c = cluster(load_buildings()) if c is None else c
    terms = _method_terms(c, carbon=carbon, pipe_mult=pipe_mult)
    edges, total, seed = _best_tree(c, terms, pipe_mult)
    cand = _network_result(c, terms, edges, seed, pipe_mult) if edges else _network_result(c, terms, [], None)
    net = cand if cand["npv_usd"] > 0 else _network_result(c, terms, [], None)
    net["best_candidate"] = {k: cand[k] for k in ("buildings", "schools", "homes", "pipe_km", "trunk_km", "delivered_mwh",
                                                  "capex_usd", "npv_usd", "npv_with_carbon_usd", "co2_avoided_t",
                                                  "methods") if k in cand}
    if cand["connected"] and cand["pipe_km"]:
        net["best_candidate"]["mwh_per_m"] = round(cand["delivered_mwh"] / (cand["pipe_km"] * 1000), 2)
    return net


def network_breakeven(c: pd.DataFrame) -> dict:
    """What would make a building network pay: a carbon price, or cheaper pipe (bisection on a cost multiplier)."""
    with_carbon = design_network(c, carbon=True)
    lo, hi = 0.0, 1.0
    if design_network(c, pipe_mult=1.0)["connected"]:
        mult = 1.0
    elif not design_network(c, pipe_mult=0.05)["connected"]:
        mult = None
    else:
        for _ in range(12):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if design_network(c, pipe_mult=mid)["connected"] else (lo, mid)
        mult = lo
    return {"pays_with_carbon_price": with_carbon["connected"],
            "with_carbon": {k: with_carbon[k] for k in ("buildings", "pipe_km", "delivered_mwh", "npv_usd", "methods")},
            "pipe_cost_breakeven_share": round(mult, 3) if mult is not None else None}


def linear_heat_density(c: pd.DataFrame) -> float:
    """MWh/yr per metre if every cluster were piped: minimum spanning tree from the site plus street mains."""
    site, d = _cfgs()
    lat = np.r_[site["site"]["lat"], c["lat"].to_numpy()]
    lon = np.r_[site["site"]["lon"], c["lon"].to_numpy()]
    n = len(lat)
    inside = np.zeros(n, bool); inside[0] = True
    best = _route_km(lat[0], lon[0], lat, lon)
    total = 0.0
    for _ in range(n - 1):
        cand = np.where(inside, np.inf, best)
        j = int(np.argmin(cand))
        total += cand[j]
        inside[j] = True
        best = np.minimum(best, _route_km(lat[j], lon[j], lat, lon))
    metres = total * 1000 * d["network"]["detour_factor"] + c["n"].sum() * d["network"]["street_m_per_building"]
    return float(c["mwh"].sum() / metres)


def _network_result(c: pd.DataFrame, terms: dict, edges: list[dict], seed, pipe_mult: float = 1.0) -> dict:
    """Exact totals for the chosen tree: each pipe sized for everything downstream of it."""
    site, d = _cfgs()
    f = d["finance"]
    pvf = _pvf(f["discount_rate"], f["horizon_years"])
    om = f["om_share_of_capex"]
    if not edges:
        return {"connected": False, "clusters": [], "edges": [], "buildings": 0, "pipe_km": 0.0,
                "delivered_mwh": 0.0, "source_mwh": 0.0, "capex_usd": 0.0, "npv_usd": 0.0,
                "npv_with_carbon_usd": 0.0, "co2_avoided_t": 0.0, "peak_mw": 0.0, "methods": {},
                "nearest_cluster_km": float(c["site_km"].min()), "trunk_km": 0.0, "seed": None}
    idx = [e["cluster"] for e in edges]
    down = {i: float(c.at[i, "peak_mw"]) for i in idx}
    for e in reversed(edges):                       # children were added after their parents
        if e["parent"] >= 0 and e["parent"] in down:
            down[e["parent"]] += down[e["cluster"]]
    capex = annual = co2 = src = E = 0.0
    out_edges, methods = [], {}
    for e in edges:
        i, k = e["cluster"], e["method"]
        t = terms[k]
        pipe = d["methods"][k]["pipe_usd_per_m"] * pipe_mult * float(_size_factor(down[i])) * e["pipe_m"]
        capex += pipe + float(t["capex_fixed"][i])
        annual += float(t["annual"][i])
        co2 += float(t["co2"][i])
        src += float(t["src"][i])
        E += float(c.at[i, "mwh"])
        methods[k] = methods.get(k, 0) + int(c.at[i, "n"])
        p = e["parent"]
        out_edges.append({"cluster": i, "method": k, "pipe_m": round(e["pipe_m"]),
                          "from": [float(c.at[p, "lat"]), float(c.at[p, "lon"])] if p >= 0
                          else [site["site"]["lat"], site["site"]["lon"]],
                          "to": [float(c.at[i, "lat"]), float(c.at[i, "lon"])]})
    npv = annual * pvf - capex * (1 + om * pvf)
    cl = c.loc[idx]
    trunk = next(e for e in edges if e["parent"] < 0)
    return {
        "connected": True, "seed": int(seed) if seed is not None else None,
        "clusters": [{"cluster": int(i), "lat": float(r.lat), "lon": float(r.lon), "buildings": int(r.n),
                      "mwh": round(float(r.mwh)), "peak_mw": round(float(r.peak_mw), 2), "top": r.top,
                      "schools": int(r.schools), "homes": int(r.homes),
                      "method": next(e["method"] for e in edges if e["cluster"] == i)}
                     for i, r in cl.iterrows()],
        "edges": out_edges, "buildings": int(cl["n"].sum()), "schools": int(cl["schools"].sum()),
        "homes": int(cl["homes"].sum()), "pipe_km": round(sum(e["pipe_m"] for e in edges) / 1000, 2),
        "trunk_km": round(trunk["pipe_m"] / 1000, 2), "peak_mw": round(float(cl["peak_mw"].sum()), 2),
        "delivered_mwh": round(E), "source_mwh": round(src), "capex_usd": round(capex),
        "annual_net_usd": round(annual), "npv_usd": round(npv), "co2_avoided_t": round(co2),
        "npv_with_carbon_usd": round(npv + co2 * f["carbon_price_usd_per_t"] * pvf),
        "methods": methods, "nearest_cluster_km": float(c["site_km"].min()),
    }


# ------------------------------------------------------------------------------- other paths

def trucked(b: pd.DataFrame, connected_ids: set) -> dict:
    """Large users off the network, served by mobile thermal storage."""
    site, d = _cfgs()
    t, f = d["methods"]["trucked"], d["finance"]
    pvf = _pvf(f["discount_rate"], f["horizon_years"])
    big = b[(b["mwh"] >= t["min_mwh_per_year"]) & ~b.index.isin(connected_ids)].copy()
    road_km = _route_km(site["site"]["lat"], site["site"]["lon"], big["lat"].to_numpy(),
                        big["lon"].to_numpy()) * d["network"]["detour_factor"]
    big["road_km"] = road_km
    big["cost_usd_mwh"] = t["usd_per_mwh_fixed"] + t["usd_per_mwh_km"] * road_km
    big["margin_usd_mwh"] = big["value_usd_mwh"] - big["cost_usd_mwh"]
    big["npv_usd"] = big["margin_usd_mwh"] * big["mwh"] * pvf
    win = big[big["npv_usd"] > 0]
    co2 = float((win["mwh"] * win["co2_t_mwh"]).sum())
    return {"candidates": int(len(big)), "served": int(len(win)), "delivered_mwh": round(float(win["mwh"].sum())),
            "npv_usd": round(float(win["npv_usd"].sum())), "co2_avoided_t": round(co2),
            "npv_with_carbon_usd": round(float(win["npv_usd"].sum()) + co2 * f["carbon_price_usd_per_t"] * pvf),
            "users": [{"name": r["name"], "road_km": round(r["road_km"], 1), "mwh": round(r["mwh"]),
                       "west_shore": bool(_west(r["lat"], r["lon"])),
                       "cost_usd_mwh": round(r["cost_usd_mwh"], 1), "value_usd_mwh": round(r["value_usd_mwh"], 1)}
                      for _, r in big.sort_values("npv_usd", ascending=False).head(8).iterrows()]}


def _anchor_shapes(h: pd.DataFrame) -> dict[str, np.ndarray]:
    _, d = _cfgs()
    g = d["anchors"]["greenhouse"]
    heat = np.clip((g["balance_c"] - h["outdoor_c"].to_numpy()) / (g["balance_c"] + 15.0), 0, 1)
    return {"greenhouse": g["base_share"] + (1 - g["base_share"]) * heat,
            "aquaculture": np.ones(len(h)), "drying": np.ones(len(h))}


def anchors(h: pd.DataFrame, leftover: np.ndarray) -> list[dict]:
    """Size each co-located user against the heat left every hour, best value per MW first, up to its cap."""
    site, d = _cfgs()
    a, f = d["anchors"], d["finance"]
    pvf = _pvf(f["discount_rate"], f["horizon_years"])
    om = f["om_share_of_capex"]
    elec = site["prices"]["electricity_usd_per_mwh"]
    grid = site["emissions"]["grid_t_per_mwh"]
    fuel = d["fuels"][a["displaced_fuel"]]
    co2_mwh = fuel["t_co2_per_mwh"] / fuel["boiler_eff"]
    shapes = _anchor_shapes(h)
    room = leftover.copy()
    out = []
    for key in ("greenhouse", "aquaculture", "drying"):
        spec, s = a[key], shapes[key]
        boost = 1.0 / spec["booster_cop"] if "booster_cop" in spec else 0.0
        # largest peak P with P * s(h) <= room(h) every hour, up to the cap
        fit = float(np.min(np.where(s > 0, room / s, np.inf)))
        unit = spec.get("mw_per_unit")
        P = max(0.0, min(spec["max_mw"], fit))
        if unit:
            P = math.floor(P / unit) * unit
        E = float((P * s).sum())                       # MWh/yr of heat taken
        elec_mwh = E * boost + E * d["network"]["pump_kwh_per_mwh"] / 1000
        capex = a["station_usd_per_kw"] * P * 1000 + a["pipe_usd_per_m"] * a["pipe_km"] * 1000 * float(_size_factor(P))
        annual = E * a["heat_price_usd_per_mwh"] - elec_mwh * elec
        co2 = E * co2_mwh - elec_mwh * grid
        npv = annual * pvf - capex * (1 + om * pvf)
        room = room - P * s
        out.append({"key": key, "label": spec["label"], "temp_c": spec["temp_c"], "peak_mw": round(P, 1),
                    "delivered_mwh": round(E), "capex_usd": round(capex), "annual_net_usd": round(annual),
                    "npv_usd": round(npv), "co2_avoided_t": round(co2),
                    "npv_with_carbon_usd": round(npv + co2 * f["carbon_price_usd_per_t"] * pvf),
                    "acres": round(P / spec["peak_mw_per_acre"], 1) if key == "greenhouse" else None,
                    "hourly_mw": P * s})
    return out


def pit_location(net: dict) -> dict:
    """At the site the pit buffers everything; at the load end it lets the trunk be sized below peak."""
    site, d = _cfgs()
    st = site["storage"]
    if not net["connected"]:
        return {"recommend": "site", "why": "No building network pays off, so the pit stays at the site and buffers "
                                            "the co-located users.", "trunk_saving_usd": 0}
    method = max(net["methods"], key=net["methods"].get)
    peak = net["peak_mw"]
    reduced = max(peak - st["max_discharge_mw"], 0.3 * peak)
    saving = d["methods"][method]["pipe_usd_per_m"] * net["trunk_km"] * 1000 * float(
        _size_factor(peak) - _size_factor(reduced))
    load_end = saving > 0.05 * net["capex_usd"]
    return {"recommend": "load end of the trunk" if load_end else "site",
            "trunk_saving_usd": round(saving), "trunk_peak_mw": round(peak, 1), "trunk_peak_with_pit_mw": round(reduced, 1),
            "why": (f"A pit at the load end covers {peak - reduced:.1f} MW of the {peak:.1f} MW peak, so the "
                    f"{net['trunk_km']} km trunk can be sized smaller (saves ${saving / 1e6:.1f}M)."
                    if load_end else
                    f"Moving the pit to the load end would save only ${saving / 1e6:.2f}M of trunk pipe; at the "
                    f"site it also buffers the co-located users.")}


# ---------------------------------------------------------------------------------- recommend

def recommend(site: str | None = None) -> dict:
    """site: "lansing" (default) or a sites.py id."""
    with using(site):
        return _recommend()


def _recommend() -> dict:
    site, d = _cfgs()
    radius = _radius_km()
    b = load_buildings()
    h = load_hourly()
    c = cluster(b)
    net = design_network(c)
    be = network_breakeven(c)
    lhd = linear_heat_density(c)

    # hourly: captured heat the network draws, then what is left for co-located users
    avail = h["avail_mw"].to_numpy()
    net_mw = net["source_mwh"] * h["bldg_shape"].to_numpy()
    leftover = np.maximum(avail - net_mw, 0.0)
    anc = anchors(h, leftover)
    taken = net_mw + sum(x["hourly_mw"] for x in anc)
    reused = np.minimum(taken, avail)

    connected = set()
    if net["connected"]:
        cl = {(int(r.cx), int(r.cy)) for _, r in c.loc[[x["cluster"] for x in net["clusters"]]].iterrows()}
        cell = d["network"]["cluster_m"] / 1000.0
        lat0, lon0 = site["site"]["lat"], site["site"]["lon"]
        bx = np.floor((b["lon"] - lon0) * 111.0 * math.cos(math.radians(lat0)) / cell).astype(int)
        by = np.floor((b["lat"] - lat0) * 111.0 / cell).astype(int)
        connected = set(b.index[[(x, y) in cl for x, y in zip(bx, by)]])
    trk = trucked(b, connected)

    pathways = [{
        "key": "network", "label": "District network to existing buildings",
        "detail": (f"{net['buildings']} buildings ({net['schools']} schools, {net['homes']} homes) in "
                   f"{len(net['clusters'])} clusters, {net['pipe_km']} km of pipe; methods: "
                   + ", ".join(f"{d['methods'][k]['label']} ({v} bldgs)" for k, v in net["methods"].items())
                   if net["connected"] else
                   f"No network pays off. The {int(b['n_buildings'].sum()) if 'n_buildings' in b else len(b):,} buildings "
                   f"within {radius:g} km{' (mostly single-family homes)' if _mostly_homes(b) else ''} use "
                   f"{b['mwh'].sum() / 1000:,.0f} GWh/yr, about {lhd:.2f} MWh per metre of pipe if all were "
                   f"connected, below the ~{d['network']['viable_mwh_per_m']} MWh/m district heat needs. The best "
                   f"network found ({net['best_candidate'].get('buildings', 0)} buildings, "
                   f"{net['best_candidate'].get('pipe_km', 0)} km) loses "
                   f"${-net['best_candidate'].get('npv_usd', 0) / 1e6:,.1f}M"
                   + (f"; it pays at a ${d['finance']['carbon_price_usd_per_t']:.0f}/t carbon price"
                      if be["pays_with_carbon_price"] else "; it does not pay even at the carbon price")
                   + (f", or if pipe cost fell to {be['pipe_cost_breakeven_share']:.0%} of today's"
                      if be["pipe_cost_breakeven_share"] else "")),
        "delivered_mwh": net["delivered_mwh"], "capex_usd": net["capex_usd"], "npv_usd": net["npv_usd"],
        "npv_with_carbon_usd": net["npv_with_carbon_usd"], "co2_avoided_t": net["co2_avoided_t"],
        "peak_mw": net["peak_mw"]}]
    pathways += [{"key": x["key"], "label": x["label"],
                  "detail": (f"{x['peak_mw']} MW peak ({x['acres']} acres)" if x["acres"] else f"{x['peak_mw']} MW")
                  + f", takes {x['temp_c']} C heat directly at the site",
                  "delivered_mwh": x["delivered_mwh"], "capex_usd": x["capex_usd"], "npv_usd": x["npv_usd"],
                  "npv_with_carbon_usd": x["npv_with_carbon_usd"], "co2_avoided_t": x["co2_avoided_t"],
                  "peak_mw": x["peak_mw"]} for x in anc]
    pathways.append({"key": "trucked", "label": d["methods"]["trucked"]["label"],
                     "detail": f"{trk['served']} of {trk['candidates']} large off-network users pay off",
                     "delivered_mwh": trk["delivered_mwh"], "capex_usd": 0, "npv_usd": trk["npv_usd"],
                     "npv_with_carbon_usd": trk["npv_with_carbon_usd"], "co2_avoided_t": trk["co2_avoided_t"],
                     "peak_mw": None})
    for p in pathways:
        p["verdict"] = ("recommend" if p["npv_usd"] > 0 and p["delivered_mwh"] > 0 else
                        "only with a carbon price" if p["npv_with_carbon_usd"] > 0 and p["delivered_mwh"] > 0 else
                        "not recommended")
    pathways.sort(key=lambda p: p["npv_usd"], reverse=True)

    pit = pit_location(net)
    avail_mwh = float(avail.sum())
    reused_mwh = float(reused.sum())
    rec = [p for p in pathways if p["verdict"] == "recommend"]
    lines = [f"Reuse {reused_mwh / avail_mwh:.0%} of the site's {avail_mwh / 1000:,.0f} GWh/yr of captured heat "
             f"({reused_mwh / 1000:,.0f} GWh/yr), for a combined 20-year NPV of "
             f"${sum(p['npv_usd'] for p in rec) / 1e6:,.1f}M and "
             f"{sum(p['co2_avoided_t'] for p in rec):,.0f} t CO2/yr avoided:"]
    lines += [f"{i}. {p['label']}: {p['detail']}. {p['delivered_mwh'] / 1000:,.1f} GWh/yr, NPV "
              f"${p['npv_usd'] / 1e6:,.1f}M." for i, p in enumerate(rec, 1)]
    lines.append(f"Storage pit: {pit['recommend']}. {pit['why']}")
    for p in pathways:
        if p["verdict"] != "recommend":
            lines.append(f"Not now: {p['label'].lower()} ({p['verdict']}; NPV ${p['npv_usd'] / 1e6:,.1f}M). {p['detail']}.")
    lines.append(f"Still rejected: {(avail_mwh - reused_mwh) / 1000:,.0f} GWh/yr. The site makes far more low-grade "
                 f"heat than anything within reach can use; that is the case for co-locating heat users here.")

    for x in anc:
        x.pop("hourly_mw")
    return {
        "site": site["site"]["name"], "site_id": site["site"]["id"],
        "location": {"lat": site["site"]["lat"], "lon": site["site"]["lon"],
                     "it_mw": site["data_center"]["it_load_mw"]},
        "summary": {"heat_available_gwh": round(avail_mwh / 1000, 1), "heat_reused_gwh": round(reused_mwh / 1000, 1),
                    "share_reused": round(reused_mwh / avail_mwh, 4),
                    "npv_recommended_usd": round(sum(p["npv_usd"] for p in rec)),
                    "co2_avoided_t": round(sum(p["co2_avoided_t"] for p in rec)),
                    "buildings_within_15km": int(b["n_buildings"].sum()) if "n_buildings" in b else int(len(b)),
                    "radius_km": radius, "demand_estimated": bool("estimated" in b), "buildings_gwh": round(float(b["mwh"].sum()) / 1000, 1),
                    "sim_route_km": site["heat_network"]["route_km"],
                    "design_route_km": net["pipe_km"] if net["connected"] else None},
        "recommendation": lines,
        "pathways": pathways,
        "network": net,
        "network_breakeven": be,
        "linear_heat_density_mwh_per_m": round(lhd, 3),
        "trucked": trk,
        "anchors": anc,
        "pit": pit,
        "hourly": {"months": _monthly(avail, net_mw, anc, h)},
        "assumptions": "config/design.yaml (every cost has a source; most are 'assumption - verify')",
    }


def _monthly(avail, net_mw, anc, h) -> list[dict]:
    idx = pd.date_range("2025-01-01", periods=len(avail), freq="h")
    df = pd.DataFrame({"avail": avail, "network": net_mw}, index=idx)
    shapes = _anchor_shapes(h)
    for x in anc:
        df[x["key"]] = x["peak_mw"] * shapes[x["key"]]
    m = df.resample("MS").sum() / 1000
    return [{"month": i.month, **{k: round(float(v), 2) for k, v in r.items()}} for i, r in m.iterrows()]


def _mostly_homes(b: pd.DataFrame) -> bool:
    w = b["n_buildings"] if "n_buildings" in b else pd.Series(1, index=b.index)
    return float(w[b["category"].isin(["Residential", "Farm residence"])].sum()) > 0.5 * float(w.sum())


def geojson(rec: dict | None = None, site: str | None = None) -> dict:
    """Site, building clusters (connected or not) and pipe segments, for a map."""
    with using(site):
        return _geojson(rec)


def _geojson(rec: dict | None = None) -> dict:
    site, _ = _cfgs()
    rec = rec or recommend()
    net = rec["network"]
    on = {x["cluster"] for x in net["clusters"]}
    c = cluster(load_buildings())
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [site["site"]["lon"], site["site"]["lat"]]},
              "properties": {"kind": "site", "name": site["site"]["name"]}}]
    for i, r in c.iterrows():
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(r.lon), float(r.lat)]},
                      "properties": {"kind": "cluster", "connected": i in on, "buildings": int(r.n),
                                     "west_shore": bool(r.west_shore),
                                     "mwh": round(float(r.mwh)), "top": r.top, "km": round(float(r.site_km), 1)}})
    for e in net["edges"]:
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": [e["from"][::-1], e["to"][::-1]]},
                      "properties": {"kind": "pipe", "method": e["method"], "pipe_m": e["pipe_m"]}})
    return {"type": "FeatureCollection", "features": feats}


if __name__ == "__main__":
    import json
    r = recommend()
    print("\n".join(r["recommendation"]))
    print(json.dumps(r["summary"], indent=2))
