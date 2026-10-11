"""Site registry and builder: model any location, not just Lansing.

    create_site("Ashburn, VA", it_mw=100) -> "ashburn_va"      (synchronous; the API runs it in a thread)
    start_site(...) -> site_id                                   (reserve the id, build in a background thread)
    list_sites(), read_meta(id), paths(id), load_cfg(id)

Lansing is the default site "lansing" and keeps its original files (config/site.yaml, data/*.csv,
web/geo/lansing.json). A new site lives in sites/<id>/ with the same file shapes:
  site.yaml      same keys as config/site.yaml (site.id/name/lat/lon, data_center.it_load_mw)
  supply.csv     Lansing's supply model output scaled by it_mw / 100 (COP unchanged)
  demand.csv     ESTIMATED heat demand of the buildings within radius_km (OSM footprints x type intensity,
                 hourly shape from the Open-Meteo 2023 temperature)
  customers.csv  the same buildings, in the columns design.py reads
  meta.json      name, address, lat, lon, it_mw, created, status, progress, sources, notes, headline
  raw/           cached raw responses (Nominatim, Open-Meteo, Overpass)
and web/geo/<id>.json for the 3D map (schema of web/geo/lansing.json, data center at (0, 0)).

Sources are free and keyless: Nominatim (geocode), Open-Meteo archive (weather), Overpass (buildings,
water). Every request has a descriptive User-Agent, a short timeout and is cached under raw/.
"""
from __future__ import annotations

import copy
import json
import math
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
SITES_DIR = ROOT / "sites"
GEO_DIR = ROOT / "web" / "geo"
DEFAULT_SITE = "lansing"
ID_RE = re.compile(r"^[a-z0-9_]{1,48}$")
STEPS = ["geocoding", "weather", "buildings", "demand", "simulating", "streaming to Keewano", "designing", "ready"]

USER_AGENT = "Alpine-heat-reuse/0.1 (SpeedHacks 2026 hackathon prototype; github.com/myramay/alpine)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                 "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
                 "https://overpass.private.coffee/api/interpreter"]
ATTRIBUTION = "© OpenStreetMap contributors (ODbL); Open-Meteo"

HDD_REF = 3500.0        # degree-days (18 C base) the intensities below are calibrated to; assumption - verify
DHW_SHARE = 0.15        # flat hot-water share of annual heat; assumption - verify
BALANCE_C = 16.0        # space heat ~ max(0, 16 C - T)
P_BAND = 0.35           # p10 / p90 = p50 x (1 -/+ 0.35); assumption - verify
MAX_ROWS = 7500         # customers.csv cap: small homes are aggregated into blocks above this
MAX_TILE_BUILDINGS = 4000
MAX_CANDIDATES_GEO = 80

_lock = threading.Lock()
_sleep = time.sleep     # tests patch this


# ------------------------------------------------------------------------------------- registry

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_id(site: str | None) -> str:
    """A safe, existing site id (default lansing). Raises ValueError otherwise."""
    site = site or DEFAULT_SITE
    if not isinstance(site, str) or not ID_RE.match(site):
        raise ValueError(f"invalid site id {site!r}")
    if site != DEFAULT_SITE and not (SITES_DIR / site / "site.yaml").exists():
        raise ValueError(f"unknown or unfinished site {site!r}")
    return site


def paths(site: str = DEFAULT_SITE) -> dict[str, Path]:
    if site == DEFAULT_SITE:
        return {"site": ROOT / "config" / "site.yaml", "supply": ROOT / "data" / "supply.csv",
                "demand": ROOT / "data" / "demand.csv", "customers": ROOT / "data" / "customers.csv",
                "geo": GEO_DIR / "lansing.json", "dir": ROOT}
    if not ID_RE.match(site or ""):
        raise ValueError(f"invalid site id {site!r}")
    d = SITES_DIR / site
    return {"site": d / "site.yaml", "supply": d / "supply.csv", "demand": d / "demand.csv",
            "customers": d / "customers.csv", "geo": GEO_DIR / f"{site}.json", "dir": d,
            "meta": d / "meta.json", "raw": d / "raw"}


def load_cfg(site: str = DEFAULT_SITE) -> dict:
    return yaml.safe_load(paths(check_id(site))["site"].read_text())


def read_meta(site: str) -> dict | None:
    if site == DEFAULT_SITE:
        cfg = yaml.safe_load(paths(DEFAULT_SITE)["site"].read_text())
        s = cfg["site"]
        return {"id": DEFAULT_SITE, "name": s["name"], "address": "Lansing, NY", "lat": s["lat"], "lon": s["lon"],
                "it_mw": cfg["data_center"]["it_load_mw"], "created": None, "status": "ready", "step": "ready",
                "example": True, "radius_km": 15,
                "sources": {"supply": "supply model (data/supply.csv)",
                            "demand": "quantile demand model + HeatOS parcel data (data/customers.csv)",
                            "weather": "NOAA Ithaca", "map": "OpenStreetMap, US Census"},
                "notes": ["Worked example: modelled demand from real parcel data."]}
    if not ID_RE.match(site or ""):
        return None
    p = SITES_DIR / site / "meta.json"
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, ValueError):
        return None


def _write_meta(site: str, **upd) -> dict:
    with _lock:
        p = SITES_DIR / site / "meta.json"
        try:
            meta = json.loads(p.read_text())
        except (FileNotFoundError, ValueError):
            meta = {"id": site}
        meta.update(upd, updated=_now())
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=1, default=str))
        tmp.replace(p)
        return meta


def set_status(site: str, step: str, **extra) -> dict:
    i = STEPS.index(step) if step in STEPS else 0
    status = "ready" if step == "ready" else "error" if step == "error" else "building"
    return _write_meta(site, status=status, step=step, progress=round(i / (len(STEPS) - 1), 3), **extra)


def list_sites() -> list[dict]:
    """Lansing first, then every site in sites/ by creation time."""
    out = [read_meta(DEFAULT_SITE)]
    rest = [m for m in (read_meta(p.name) for p in sorted(SITES_DIR.glob("*")) if p.is_dir()) if m]
    rest.sort(key=lambda m: str(m.get("created") or ""))
    return out + rest


def slugify(text: str, taken: set[str] | None = None) -> str:
    taken = set(taken if taken is not None else {p.name for p in SITES_DIR.glob("*")}) | {DEFAULT_SITE}
    base = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40].strip("_") or "site"
    if base[0].isdigit():
        base = "site_" + base
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}_{n}", n + 1
    return sid


# ------------------------------------------------------------------------------------- fetching

def _http():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), headers={"User-Agent": USER_AGENT},
                        follow_redirects=True)


def _cached(raw: Path | None, name: str, fetch):
    """JSON from raw/<name> if cached, else fetch() and cache it."""
    p = raw / name if raw else None
    if p and p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    data = fetch()
    if p:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
    return data


def geocode(query: str, raw: Path | None = None) -> dict:
    def fetch():
        with _http() as c:
            r = c.get(NOMINATIM_URL, params={"format": "json", "q": query, "limit": 1})
            r.raise_for_status()
            return r.json()
    res = _cached(raw, "nominatim.json", fetch)
    if not res:
        raise LookupError(f"address not found: {query[:120]}")
    return {"lat": float(res[0]["lat"]), "lon": float(res[0]["lon"]),
            "display_name": res[0].get("display_name", query)}


def fetch_weather(lat: float, lon: float, raw: Path | None = None) -> np.ndarray:
    """8760 hourly outdoor temperatures (C), 2023, UTC. Feb 29 dropped, gaps interpolated."""
    def fetch():
        with _http() as c:
            r = c.get(OPEN_METEO_URL, params={"latitude": round(lat, 4), "longitude": round(lon, 4),
                                              "start_date": "2023-01-01", "end_date": "2023-12-31",
                                              "hourly": "temperature_2m", "timezone": "UTC"})
            r.raise_for_status()
            return r.json()
    data = _cached(raw, "open_meteo.json", fetch)
    s = pd.Series(data["hourly"]["temperature_2m"], index=pd.to_datetime(data["hourly"]["time"]), dtype=float)
    s = s[~((s.index.month == 2) & (s.index.day == 29))]
    full = pd.date_range("2023-01-01", periods=8760, freq="h")
    s = s[~s.index.duplicated()].reindex(full).interpolate(limit_direction="both")
    if s.isna().all():
        raise ValueError("Open-Meteo returned no temperatures")
    return s.fillna(s.mean()).to_numpy()


def overpass(query: str, raw: Path | None = None, name: str = "overpass.json", tries: int = 2) -> dict:
    """POST an Overpass QL query; main server then the mirror, `tries` rounds with a short backoff."""
    def fetch():
        import httpx
        last = None
        for rnd in range(tries):
            for url in OVERPASS_URLS:
                try:
                    with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0),
                                      headers={"User-Agent": USER_AGENT}) as c:
                        r = c.post(url, data={"data": query})
                    if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                        return r.json()
                    last = f"{url.split('/')[2]}: HTTP {r.status_code}"
                except Exception as e:  # noqa: BLE001
                    last = f"{url.split('/')[2]}: {type(e).__name__}"
                _sleep(2.0)
            _sleep(10.0 * (rnd + 1))
        raise RuntimeError(f"OpenStreetMap Overpass is unavailable right now ({last}); try again in a few minutes")
    return _cached(raw, name, fetch)


def overpass_buildings(lat: float, lon: float, radius_m: float, raw: Path | None = None, n: int = 3) -> dict:
    """Building ways within radius_m (`out tags geom`: bounds + exact footprint), fetched as an n x n grid of bboxes: small queries get
    through a loaded Overpass where one big one times out. Each cell is cached, so a retry resumes."""
    dlat, dlon = radius_m / 110540.0, radius_m / (111320.0 * math.cos(math.radians(lat)))
    seen, els = set(), []
    for i in range(n):
        for j in range(n):
            s_, w_ = lat - dlat + 2 * dlat * i / n, lon - dlon + 2 * dlon * j / n
            n_, e_ = s_ + 2 * dlat / n, w_ + 2 * dlon / n
            q = f'[out:json][timeout:90];way["building"]({s_:.6f},{w_:.6f},{n_:.6f},{e_:.6f});out tags geom;'
            data = overpass(q, raw, f"overpass_bldg_geom_{i}{j}.json", tries=3)
            for el in data.get("elements", []):
                if el.get("id") in seen:
                    continue
                seen.add(el.get("id"))
                bb = el.get("bounds")
                if bb:
                    clat, clon = (bb["minlat"] + bb["maxlat"]) / 2, (bb["minlon"] + bb["maxlon"]) / 2
                    if math.hypot((clat - lat) * 110540.0, (clon - lon) * 111320.0 * math.cos(math.radians(lat))) > radius_m:
                        continue
                els.append(el)
            _sleep(1.0)
    return {"elements": els}


# ---------------------------------------------------------------------------- building model

# type -> default levels, heat intensity kWh/m2.yr (at HDD_REF), NREL type, category, map use, default height m
TYPES = {
    "house":      (1.5, 110, "single-family_detached", "Residential", "home", 6.5),
    "terrace":    (2.0, 110, "single-family_attached", "Residential", "home", 8.0),
    "apartments": (3.5, 110, "multi-family_with_5plus_units", "Residential", "residential", 12.0),
    "office":     (2.0, 90, "smalloffice", "Office", "office", 9.0),
    "retail":     (1.0, 120, "retailstandalone", "Other", "retail", 6.0),
    "food":       (1.0, 150, "fullservicerestaurant", "Other", "food", 5.0),
    "school":     (2.0, 100, "primaryschool", "Education", "school", 8.0),
    "college":    (3.0, 100, "secondaryschool", "Education", "school", 11.0),
    "clinic":     (3.0, 150, "outpatient", "Community/Other", "clinic", 10.0),
    "hospital":   (4.0, 180, "hospital", "Community/Other", "clinic", 14.0),
    "hotel":      (4.0, 110, "smallhotel", "Hotel", "residential", 13.0),
    "civic":      (1.0, 90, "smalloffice", "Community/Other", "civic", 8.0),
    "industrial": (1.0, 60, "warehouse", "Other", "industrial", 10.0),
}
INTENSITY_NOTE = ("residential 110, office 90, school 100, retail 120, restaurant 150, clinic 150, hospital 180, "
                  "industrial/warehouse 60 kWh/m2.yr at 3,500 HDD (18 C base); assumption - verify")
SKIP = {"data_center", "garage", "garages", "shed", "carport", "roof", "parking", "construction", "hut", "ruins",
        "service", "transformer_tower", "storage_tank", "greenhouse", "barn", "farm_auxiliary", "kiosk", "bunker",
        "toilets", "container", "tent", "silo", "bridge", "cowshed", "stable", "boathouse", "no", "demolished",
        "abandoned", "proposed", "pavilion", "grandstand", "gatehouse", "water_tower", "power", "substation"}
DC_NAME = re.compile(r"data ?cent(er|re)|datacenter|equinix|digital realty|coresite|cyrusone|ragingwire|raging wire|"
                     r"cloudhq|sabey|\bqts\b|stack infrastructure|vantage data|aligned data|iron mountain data|"
                     r"\bdc ?\d", re.I)
HOUSE = {"house", "detached", "semidetached_house", "bungalow", "cabin", "farm", "static_caravan", "residential"}


def classify(tags: dict, area_m2: float) -> str | None:
    """Building type key (TYPES) or None when it has no heat demand (data centers, garages, sheds...)."""
    b = (tags.get("building") or "yes").lower()
    name = tags.get("name") or ""
    if b in SKIP or tags.get("telecom") == "data_center" or DC_NAME.search(name):
        return None
    am, shop = (tags.get("amenity") or "").lower(), tags.get("shop")
    if b in ("school", "kindergarten") or am in ("school", "kindergarten"):
        return "school"
    if b in ("university", "college") or am in ("university", "college"):
        return "college"
    if b == "hospital" or am == "hospital":
        return "hospital"
    if am in ("clinic", "doctors", "dentist"):
        return "clinic"
    if am in ("restaurant", "fast_food", "cafe", "pub", "bar"):
        return "food"
    if b in ("hotel", "motel", "dormitory") or tags.get("tourism") in ("hotel", "motel"):
        return "hotel"
    if b in ("retail", "supermarket", "kiosk") or shop:
        return "retail"
    if b in ("office", "commercial") or tags.get("office"):
        return "office"
    if b in ("industrial", "warehouse", "manufacture", "factory", "hangar"):
        return "industrial"
    if b in ("church", "chapel", "mosque", "temple", "synagogue", "civic", "public", "government", "fire_station",
             "sports_hall", "library", "community_centre") or am in ("place_of_worship", "library", "townhall",
                                                                     "community_centre", "fire_station"):
        return "civic"
    if b in ("apartments",):
        return "apartments"
    if b in ("terrace", "townhouse"):
        return "terrace"
    if b in HOUSE:
        return "apartments" if b == "residential" and area_m2 > 600 else "house"
    # very large anonymous boxes (no name, shop, amenity or office tag): in data-center markets these are
    # mostly data halls or big warehouses, neither heated like a building; leave them out
    if (b in ("yes", "commercial", "industrial", "warehouse") and area_m2 > 10000 and not name
            and not (am or shop or tags.get("office"))):
        return None
    # building=yes or unknown: decide by size
    if area_m2 < 40:
        return None
    if area_m2 <= 300:
        return "house"
    return "office" if area_m2 <= 3000 else "industrial"


def _levels(tags: dict) -> float | None:
    v = tags.get("building:levels")
    try:
        lv = float(str(v).split(";")[0].strip())
        return lv if 0 < lv < 120 else None
    except (TypeError, ValueError):
        return None


def _height(tags: dict, t: str | None) -> float:
    v = tags.get("height")
    if v:
        try:
            h = float(re.sub(r"[^0-9.]", "", str(v).split(";")[0]) or "nan")
            if 1 < h < 500:
                return round(h, 1)
        except ValueError:
            pass
    lv = _levels(tags)
    if lv:
        return round(lv * 3.2 + 1.0, 1)
    if t is None:
        return 14.0 if (tags.get("building") == "data_center" or DC_NAME.search(tags.get("name") or "")) else 4.0
    return TYPES[t][5]


def _label(tags: dict, t: str) -> str:
    """A public label (named non-residential building, amenity, shop) or the building type. Never a
    personal name: names on homes are dropped."""
    if TYPES[t][3] != "Residential" or t == "hotel":
        nm = (tags.get("name") or "").strip()
        if nm:
            return nm[:80]
        for k in ("amenity", "shop", "office"):
            if tags.get(k) and tags[k] != "yes":
                return tags[k].replace("_", " ").capitalize()
    return {"house": "Residence", "terrace": "Townhouse", "apartments": "Apartment building",
            "office": "Commercial building", "industrial": "Industrial building"}.get(t, t.capitalize() + " building")


class Proj:
    """Local equirectangular projection, metres east/north of the origin."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110540.0

    def xy(self, lat, lon):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * self.ky

    def latlon(self, x, y):
        return self.lat0 + np.asarray(y) / self.ky, self.lon0 + np.asarray(x) / self.kx


def _poly_area(pts: list[tuple[float, float]]) -> float:
    if len(pts) < 3:
        return 0.0
    a = np.asarray(pts)
    x, y = a[:, 0], a[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def _simplify(pts: list[list[int]], tol: float = 1.5) -> list[list[int]]:
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= tol:
            out.append(p)
    return out


def parse_tile(data: dict, proj: Proj) -> tuple[list[dict], list[list[list[int]]], dict[int, dict]]:
    """Overpass `out tags geom` for the 3D tile -> (buildings, water polygons, {way id: footprint info})."""
    buildings, water, info = [], [], {}
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        geoms = []
        if el.get("geometry"):
            geoms = [el["geometry"]]
        elif el.get("members"):
            geoms = [m["geometry"] for m in el["members"] if m.get("geometry") and m.get("role") in ("outer", "")]
        for g in geoms:
            x, y = proj.xy([p["lat"] for p in g], [p["lon"] for p in g])
            pts = [[int(round(a)), int(round(b))] for a, b in zip(x, y)]
            if "building" in tags and el["type"] == "way":
                area = _poly_area(list(zip(x, y)))
                t = classify(tags, area)
                h = _height(tags, t)
                pts = _simplify(pts)
                if len(pts) >= 3:
                    buildings.append({"h": h, "p": pts, "n": _label(tags, t) if t and TYPES[t][3] != "Residential"
                                      and tags.get("name") else "", "_a": area})
                info[el["id"]] = {"area": area, "h": h}
            elif "building" not in tags:
                pts = _simplify(pts, 8)
                if len(pts) >= 3:
                    water.append(pts)
    buildings.sort(key=lambda b: -b["_a"])
    buildings = buildings[:MAX_TILE_BUILDINGS]
    for b in buildings:
        b.pop("_a")
    return buildings, water, info


def parse_candidates(data: dict, proj: Proj, tile_info: dict[int, dict] | None = None) -> pd.DataFrame:
    """Overpass `out tags bb` within radius -> one row per heated building (type, footprint, levels)."""
    tile_info = tile_info or {}
    rows = []
    for el in data.get("elements", []):
        tags, bb = el.get("tags") or {}, el.get("bounds")
        if not bb:
            continue
        lat = (bb["minlat"] + bb["maxlat"]) / 2
        lon = (bb["minlon"] + bb["maxlon"]) / 2
        ti = tile_info.get(el.get("id"))
        if ti:
            area = ti["area"]
        elif el.get("geometry"):
            gx, gy = proj.xy([p["lat"] for p in el["geometry"]], [p["lon"] for p in el["geometry"]])
            area = _poly_area(list(zip(gx, gy)))
        else:   # bounding box only: a rotated footprint fills ~60% of it
            area = (bb["maxlat"] - bb["minlat"]) * proj.ky * (bb["maxlon"] - bb["minlon"]) * proj.kx * 0.6
        t = classify(tags, area)
        if t is None or area < 20:
            continue
        x, y = proj.xy(lat, lon)
        rows.append({"osm_id": el.get("id"), "type": t, "name": _label(tags, t), "lat": lat, "lon": lon,
                     "x": float(x), "y": float(y), "area_m2": area, "levels": _levels(tags) or TYPES[t][0],
                     "h": ti["h"] if ti else _height(tags, t)})
    return pd.DataFrame(rows, columns=["osm_id", "type", "name", "lat", "lon", "x", "y", "area_m2", "levels", "h"])


def heat_shape(temps: np.ndarray, dhw_share: float = DHW_SHARE) -> np.ndarray:
    """Hourly share of annual heat (sums to 1): space heat ~ max(0, 16 C - T) plus a flat hot-water share."""
    space = np.maximum(0.0, BALANCE_C - temps)
    space = space / space.sum() if space.sum() > 0 else np.full(len(temps), 1.0 / len(temps))
    return (1 - dhw_share) * space + dhw_share / len(temps)


def hdd(temps: np.ndarray, base: float = 18.0) -> float:
    daily = temps[: len(temps) // 24 * 24].reshape(-1, 24).mean(axis=1)
    return float(np.maximum(0.0, base - daily).sum())


def estimate_demand(cands: pd.DataFrame, temps: np.ndarray, proj: Proj,
                    max_rows: int = MAX_ROWS) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Candidates -> (customers.csv, demand.csv, facts). Annual heat = floor area x intensity, the space-heat
    part scaled by this year's heating degree-days vs HDD_REF."""
    deg = hdd(temps)
    f_hdd = float(np.clip(deg / HDD_REF, 0.15, 2.0))
    shape = heat_shape(temps)
    peak_factor = float(shape.max() * len(shape))       # peak hour / mean hour
    c = cands.copy()
    if c.empty:
        raise ValueError("no heated buildings found within the radius (OpenStreetMap has no footprints here?)")
    c["floor_m2"] = c["area_m2"] * c["levels"]
    c["intensity"] = c["type"].map({k: v[1] for k, v in TYPES.items()})
    c["kwh"] = c["floor_m2"] * c["intensity"] * ((1 - DHW_SHARE) * f_hdd + DHW_SHARE)
    c["n_buildings"] = 1

    # aggregate small homes into blocks when there are too many rows for the design search
    cell = 150.0
    while len(c) > max_rows:
        homes = c["type"].isin(["house", "terrace"])
        h = c[homes].copy()
        h["cx"], h["cy"] = np.floor(h["x"] / cell), np.floor(h["y"] / cell)
        g = h.groupby(["cx", "cy"])
        blk = pd.DataFrame({
            "osm_id": g["osm_id"].first(), "type": g["type"].agg(lambda s: s.mode().iat[0]),
            "lat": g["lat"].mean(), "lon": g["lon"].mean(), "x": g["x"].mean(), "y": g["y"].mean(),
            "area_m2": g["area_m2"].sum(), "levels": g["levels"].mean(), "h": g["h"].mean(),
            "floor_m2": g["floor_m2"].sum(), "intensity": g["intensity"].mean(), "kwh": g["kwh"].sum(),
            "n_buildings": g["n_buildings"].sum()}).reset_index(drop=True)
        blk["name"] = ["Block of %d homes" % n if n > 1 else "Residence" for n in blk["n_buildings"]]
        c = pd.concat([c[~homes], blk], ignore_index=True)
        cell *= 1.6

    c = c.reset_index(drop=True)
    t = c["type"]
    cust = pd.DataFrame({
        "id": [f"S-{i:05d}" for i in range(len(c))], "name": c["name"],
        "lat": c["lat"].round(5), "lon": c["lon"].round(5),
        "dist_m": np.hypot(c["x"], c["y"]).round(1),
        "category": t.map({k: v[3] for k, v in TYPES.items()}),
        "nrel_type": t.map({k: v[2] for k, v in TYPES.items()}),
        "building_type": t, "n_buildings": c["n_buildings"].astype(int),
        "floor_m2": c["floor_m2"].round(0), "levels": c["levels"].round(1),
        "main_fuel": "unknown",
        "annual_mmbtu": (c["kwh"] / 293.071).round(3),
        "dhw_share": DHW_SHARE, "uncertainty": P_BAND,
        "current_cost_X_usd_per_mmbtu": np.nan,
        "peak_kw_p50": (c["kwh"] / 8760 * peak_factor).round(3),
        "mean_kw_p50": (c["kwh"] / 8760).round(3),
        "estimated": True,
    })
    # large offices are not "small"
    big_office = (t == "office") & (c["floor_m2"] > 5000)
    cust.loc[big_office, "nrel_type"] = "mediumoffice"

    total_mwh = float(c["kwh"].sum() / 1000)
    p50 = total_mwh * shape
    ts = pd.date_range("2023-01-01", periods=len(temps), freq="h").strftime("%Y-%m-%d %H:%M:%S")
    dem = pd.DataFrame({"timestamp": ts, "outdoor_c": np.round(temps, 2),
                        "demand_mw_p10": np.round(p50 * (1 - P_BAND), 3), "demand_mw_p50": np.round(p50, 3),
                        "demand_mw_p90": np.round(p50 * (1 + P_BAND), 3)})
    w = c["kwh"] / c["kwh"].sum()
    facts = {"hdd_18c": round(deg), "hdd_factor": round(f_hdd, 3), "peak_factor": round(peak_factor, 3),
             "buildings": int(c["n_buildings"].sum()), "rows": int(len(c)), "annual_gwh": round(total_mwh / 1000, 2),
             "weighted_dist_km": round(float((np.hypot(c["x"], c["y"]) * w).sum() / 1000), 2),
             "by_type": {k: int(v) for k, v in c.groupby("type")["n_buildings"].sum().items()}}
    return cust, dem, facts


def scaled_supply(it_mw: float) -> pd.DataFrame:
    """Lansing's supply model shape scaled to this site's IT MW (COP unchanged)."""
    s = pd.read_csv(ROOT / "data" / "supply.csv")
    k = it_mw / 100.0
    for col in s.columns:
        if col != "timestamp" and not col.startswith("cop"):
            s[col] = (s[col] * k).round(3)
    return s


def build_geo(proj: Proj, buildings: list[dict], water: list, cust: pd.DataFrame, tile_m: float) -> dict:
    x, y = proj.xy(cust["lat"].to_numpy(), cust["lon"].to_numpy())
    c = cust.assign(x=x, y=y, mwh=cust["annual_mmbtu"] * 0.293071)
    c = c[(c["x"].abs() <= tile_m) & (c["y"].abs() <= tile_m)].nlargest(MAX_CANDIDATES_GEO, "mwh")
    use = {k: v[4] for k, v in TYPES.items()}
    cands = [{"id": r.id, "name": r.name, "x": int(round(r.x)), "y": int(round(r.y)),
              "h": TYPES[r.building_type][5], "mwh": round(float(r.mwh), 1), "use": use[r.building_type],
              "connected": False} for r in c.itertuples()]
    return {"origin": [round(proj.lat0, 6), round(proj.lon0, 6)], "attribution": ATTRIBUTION,
            "buildings": buildings, "water": water, "land": [], "pipes": [], "candidates": cands}


def site_config(site_id: str, name: str, lat: float, lon: float, it_mw: float, facts: dict,
                radius_km: float) -> dict:
    """config/site.yaml as defaults; location, size and the network route set for this site."""
    cfg = copy.deepcopy(yaml.safe_load((ROOT / "config" / "site.yaml").read_text()))
    k = it_mw / 100.0
    cfg["site"].update(id=site_id, name=name, lat=round(lat, 6), lon=round(lon, 6), timezone="UTC")
    cfg["data_center"]["it_load_mw"] = float(it_mw)
    cfg["cooling"]["dry_cooler_capacity_mw"] = round(cfg["cooling"]["dry_cooler_capacity_mw"] * k, 2)
    for key in ("capacity_mwh", "max_charge_mw", "max_discharge_mw"):
        cfg["storage"][key] = round(cfg["storage"][key] * k, 2)
    cfg["heat_network"]["route_km"] = round(max(1.0, facts.get("weighted_dist_km", 3.0) * 1.3), 2)
    cfg["design_radius_km"] = radius_km
    cfg["notes"] = {
        "site": "location from the user (geocoded with Nominatim when an address was given)",
        "data_center": f"IT load {it_mw:g} MW from the user; capture, temperatures and PUE: Lansing defaults",
        "cooling": "Lansing defaults; dry cooler capacity scaled by IT MW / 100",
        "storage": "Lansing defaults scaled by IT MW / 100; assumption - verify",
        "heat_network": "route_km = demand-weighted mean building distance x 1.3; other values Lansing defaults",
        "prices": "Lansing (upstate NY) defaults; assumption - verify for this location",
        "emissions": "Lansing (upstate NY grid) defaults; assumption - verify for this location",
        "supply": "data/supply.csv (Lansing supply model, 100 MW) scaled by IT MW / 100",
        "demand": ("ESTIMATED from OpenStreetMap footprints x levels x heat intensity by building type ("
                   + INTENSITY_NOTE + "); hourly shape from Open-Meteo 2023 temperatures"),
    }
    return cfg


# ----------------------------------------------------------------------------------- the build

def _parse_latlon(text: str | None) -> tuple[float, float] | None:
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*", text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def reserve(name: str | None = None, address: str | None = None, lat: float | None = None,
            lon: float | None = None, it_mw: float = 100.0, radius_km: float = 5.0) -> str:
    """Pick a unique id and write meta.json with status queued."""
    ll = _parse_latlon(address)
    if ll and lat is None:
        lat, lon = ll
        address = None
    label = (name or address or (f"{lat:.4f}, {lon:.4f}" if lat is not None else "")).strip()
    if not label:
        raise ValueError("give an address, a name or lat/lon")
    with _lock:
        SITES_DIR.mkdir(exist_ok=True)
        sid = slugify(label if not ll or name else f"site_{lat:.3f}_{lon:.3f}")
        (SITES_DIR / sid).mkdir(parents=True)
    _write_meta(sid, id=sid, name=label, address=address, lat=lat, lon=lon, it_mw=float(it_mw),
                radius_km=float(radius_km), created=_now(), status="queued", step="queued", progress=0.0,
                steps=STEPS, sources={}, notes=[])
    return sid


def build(site_id: str, push: bool = True, tile_km: float = 1.2) -> str:
    """Run every step for a reserved site, recording progress in meta.json. Returns the site id."""
    t0 = time.monotonic()
    meta = read_meta(site_id) or {}
    p = paths(site_id)
    raw = p["raw"]
    raw.mkdir(parents=True, exist_ok=True)
    it_mw, radius_km = float(meta.get("it_mw") or 100.0), float(meta.get("radius_km") or 5.0)
    step = "geocoding"
    try:
        set_status(site_id, step, started=_now())
        lat, lon, name = meta.get("lat"), meta.get("lon"), meta.get("name")
        if lat is None or lon is None:
            g = geocode(meta.get("address") or name, raw)
            lat, lon = g["lat"], g["lon"]
            _write_meta(site_id, lat=lat, lon=lon, display_name=g["display_name"])
        proj = Proj(lat, lon)

        step = "weather"
        set_status(site_id, step)
        temps = fetch_weather(lat, lon, raw)

        step = "buildings"
        set_status(site_id, step)
        tile_m = tile_km * 1000
        q_tile = (f'[out:json][timeout:120];(way["building"](around:{tile_m:.0f},{lat},{lon}););out tags geom;'
                  f'(way["natural"="water"](around:{tile_m:.0f},{lat},{lon});'
                  f'relation["natural"="water"](around:{tile_m:.0f},{lat},{lon});'
                  f'way["waterway"="riverbank"](around:{tile_m:.0f},{lat},{lon});'
                  f'way["landuse"="reservoir"](around:{tile_m:.0f},{lat},{lon}););out geom;')
        tile = overpass(q_tile, raw, "overpass_tile.json")
        buildings, water, info = parse_tile(tile, proj)
        _sleep(1.0)
        cand_raw = overpass_buildings(lat, lon, radius_km * 1000, raw)
        cands = parse_candidates(cand_raw, proj, info)

        step = "demand"
        set_status(site_id, step)
        cust, dem, facts = estimate_demand(cands, temps, proj)
        cfg = site_config(site_id, name, lat, lon, it_mw, facts, radius_km)
        p["site"].write_text("# Generated by sites.py; every value has a note under `notes`.\n"
                             + yaml.safe_dump(cfg, sort_keys=False))
        scaled_supply(it_mw).to_csv(p["supply"], index=False)
        dem.to_csv(p["demand"], index=False)
        cust.to_csv(p["customers"], index=False)
        GEO_DIR.mkdir(parents=True, exist_ok=True)
        p["geo"].write_text(json.dumps(build_geo(proj, buildings, water, cust, tile_m), separators=(",", ":")))
        _write_meta(site_id, facts=facts, sources={
            "geocode": "Nominatim (OpenStreetMap)" if meta.get("lat") is None else "entered lat/lon",
            "weather": "Open-Meteo archive, 2023 hourly temperature_2m (UTC)",
            "buildings": f"OpenStreetMap via Overpass: {len(cands):,} heated buildings within {radius_km:g} km, "
                         f"{len(buildings):,} footprints within {tile_km:g} km for the map, {len(water)} water areas",
            "supply": f"Lansing supply model (data/supply.csv) scaled to {it_mw:g} MW IT",
            "demand": "ESTIMATED (see notes)"},
            notes=["Heat demand is ESTIMATED, not metered: OSM footprint x levels (defaults by type when untagged) x "
                   "heat intensity by building type (" + INTENSITY_NOTE + "), space heat scaled by this location's "
                   f"{facts['hdd_18c']:,} HDD vs 3,500; hourly shape max(0, 16 C - T) + 15% flat hot water; "
                   "P10/P90 = P50 -/+ 35%.",
                   "What buildings pay for heat today is unknown here: design.py uses its default fuel cost.",
                   "Prices, grid carbon and cooling constants are the Lansing (upstate NY) defaults; verify locally.",
                   "Data-center buildings, garages/sheds and anonymous boxes over 10,000 m2 (likely data halls or "
                   "warehouses) are excluded from demand."])

        step = "simulating"
        set_status(site_id, step)
        import thermal
        rid = f"normal-{datetime.now(timezone.utc):%m%d%H%M}-{__import__('uuid').uuid4().hex[:4]}"
        r = thermal.run(scenario="normal", site=site_id, push=False, save=True, run_id=rid)
        for sc in ("heat_wave", "cold_snap", "server_outage"):     # local only, for the Report comparison
            thermal.run(scenario=sc, site=site_id, push=False)
        sim = {"run_id": rid, "summary": {k: r["summary"][k] for k in ("pue", "wue_l_per_kwh", "erf",
                                                                      "share_reused", "heat_available_gwh")}}
        _write_meta(site_id, run_id=rid, sim=sim)

        step = "streaming to Keewano"
        set_status(site_id, step)
        kw = {"accepted": 0, "sent": 0, "dry_run": True}
        if push:
            try:
                import keewano
                res = keewano.send_run(r["kw_run"])
                kw = {k: res.get(k) for k in ("sent", "accepted", "batches", "seconds", "entity", "dry_run")}
            except Exception as e:  # noqa: BLE001 - Keewano is not needed for the design
                kw = {"error": _safe(e)}
        kw["events"] = len(r["kw_run"].events)
        _write_meta(site_id, keewano=kw)

        step = "designing"
        set_status(site_id, step)
        import design
        design._cfgs_for.cache_clear()       # a rebuilt site must not reuse a cached config
        headline = headline_of(design.recommend(site=site_id))
        ready_note = ("ready (Keewano data queryable in ~5 min)" if kw.get("accepted") else "ready")
        set_status(site_id, "ready", headline=headline, ready_note=ready_note,
                   seconds=round(time.monotonic() - t0, 1), finished=_now())
        return site_id
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        set_status(site_id, "error", failed_step=step, error=_safe(e), finished=_now())
        raise


def headline_of(rec: dict) -> dict:
    """The few numbers a site chip / compare row needs, from design.recommend()."""
    s = rec["summary"]
    best = next((x for x in rec["pathways"] if x["verdict"] == "recommend"), None)
    return {"heat_available_gwh": s["heat_available_gwh"], "share_reused": s["share_reused"],
            "npv_recommended_usd": s["npv_recommended_usd"], "co2_avoided_t": s["co2_avoided_t"],
            "best_pathway": best["label"] if best else None, "first_lines": rec["recommendation"][:3]}


def _safe(e: Exception) -> str:
    """An error message with anything key-like removed (never echo secrets)."""
    msg = f"{type(e).__name__}: {e}"[:400]
    return re.sub(r"(sk-[A-Za-z0-9_\-]{6,}|kwpat-[A-Za-z0-9_\-]+|[A-Za-z0-9_\-]{32,})", "[redacted]", msg)


def create_site(name: str | None = None, address: str | None = None, lat: float | None = None,
                lon: float | None = None, it_mw: float = 100.0, radius_km: float = 5.0, push: bool = True) -> str:
    """Synchronous: reserve + build. Returns the site id (raises on failure; meta.json says where)."""
    return build(reserve(name, address, lat, lon, it_mw, radius_km), push=push)


def start_site(name: str | None = None, address: str | None = None, lat: float | None = None,
               lon: float | None = None, it_mw: float = 100.0, radius_km: float = 5.0, push: bool = True) -> str:
    """Reserve now, build in a daemon thread. Poll read_meta(id)."""
    sid = reserve(name, address, lat, lon, it_mw, radius_km)

    def job():
        try:
            build(sid, push=push)
        except Exception:  # noqa: BLE001 - recorded in meta.json
            pass
    threading.Thread(target=job, name=f"site-{sid}", daemon=True).start()
    return sid


def retry_site(site_id: str, push: bool = True) -> str:
    """Rebuild a site whose build failed; cached raw responses are reused, so it resumes."""
    meta = read_meta(site_id)
    if not meta or site_id == DEFAULT_SITE:
        raise ValueError(f"no site {site_id!r}")
    if meta.get("status") != "error":
        raise ValueError(f"site {site_id!r} is {meta.get('status')}, not failed")
    _write_meta(site_id, status="queued", step="queued", progress=0.0, error=None, failed_step=None)

    def job():
        try:
            build(site_id, push=push)
        except Exception:  # noqa: BLE001 - recorded in meta.json
            pass
    threading.Thread(target=job, name=f"site-{site_id}", daemon=True).start()
    return site_id


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("place", help='address or "lat, lon"')
    ap.add_argument("--it-mw", type=float, default=100.0)
    ap.add_argument("--radius-km", type=float, default=5.0)
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    sid = create_site(address=a.place, it_mw=a.it_mw, radius_km=a.radius_km, push=not a.no_push)
    print(json.dumps(read_meta(sid), indent=1))
