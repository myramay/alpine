"""Editable assumptions: the inputs that move a site's plan, with their sources, and per-site overrides.

    registry(site)              -> [{key, file, label, unit, group, default, value, overridden, min, max,
                                     source, verify, affects}]
    grouped(site)               -> [{group, items: [...]}]       (what GET /sites/{id}/assumptions returns)
    validate(site, {key: v})    -> cleaned dict (raises ValueError with every problem)
    save(site, {key: v})        -> merges into the site's overrides (None removes one), clears caches
    reset(site)                 -> removes every override
    apply(cfg, file, site)      -> a copy of site.yaml / design.yaml config with the overrides applied
    supply_scale(site)          -> (it_ratio, heat_ratio) for overridden IT MW / capture fraction
    provenance(site, rec)       -> where every headline and pathway number came from

Defaults are the values in the site's site.yaml (config/site.yaml for Lansing, sites/<id>/site.yaml) and in
config/design.yaml. Source notes are the inline comments in config/site.yaml and config/design.yaml (and, for
a built site, its site.yaml `notes:` per section). verify = the source says "assumption - verify".
Overrides live at outputs/overrides/<id>.yaml under DATA_DIR (repo outputs/ when DATA_DIR is unset).
"""
from __future__ import annotations

import copy
import math
import re
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
VERIFY_RE = re.compile(r"assumption\s*-\s*verify", re.I)
_lock = threading.Lock()

G_SITE, G_PRICE, G_METHOD, G_ANCHOR, G_TRUCK, G_STORE = (
    "Site & data center", "Prices & finance", "Transport methods", "On-site users", "Trucking", "Storage")
GROUPS = [G_SITE, G_PRICE, G_METHOD, G_ANCHOR, G_TRUCK, G_STORE]

# key, file, label, unit, group, min, max, affects ("design": the plan; "simulation": the hourly year; "both")
SPECS: list[tuple] = [
    ("data_center.it_load_mw", "site", "IT load", "MW", G_SITE, 1, 1000, "both"),
    ("data_center.capture_fraction", "site", "Heat captured by liquid cooling", "share", G_SITE, 0.3, 0.95, "both"),
    ("heat_network.route_km", "site", "Mean network route (simulation)", "km", G_SITE, 0.1, 50, "simulation"),
    ("heat_network.pipe_loss_per_km", "site", "Network heat loss", "share/km", G_SITE, 0, 0.1, "simulation"),
    ("cooling.dry_cooler_capacity_mw", "site", "Dry cooler capacity", "MW th", G_SITE, 0, 2000, "simulation"),
    ("cooling.water_gal_per_kwh", "site", "Tower water per kWh of cooling power", "gal/kWh", G_SITE, 0, 10, "simulation"),
    ("cooling.chiller_cop", "site", "Chiller COP (towers)", "COP", G_SITE, 1, 15, "simulation"),

    ("prices.electricity_usd_per_mwh", "site", "Electricity price", "$/MWh", G_PRICE, 0, 1000, "both"),
    ("prices.water_usd_per_kgal", "site", "Water price", "$/kgal", G_PRICE, 0, 100, "simulation"),
    ("emissions.grid_t_per_mwh", "site", "Grid carbon intensity", "t CO2/MWh", G_PRICE, 0, 1.5, "both"),
    ("heat_network.heat_price_usd_per_mwh", "site", "Heat price to buildings (simulation)", "$/MWh", G_PRICE, 0, 500, "simulation"),
    ("finance.discount_rate", "design", "Discount rate", "share/yr", G_PRICE, 0.005, 0.25, "design"),
    ("finance.horizon_years", "design", "Evaluation horizon", "years", G_PRICE, 5, 50, "design"),
    ("finance.carbon_price_usd_per_t", "design", "Carbon price (\"with carbon\" NPV only)", "$/t", G_PRICE, 0, 1000, "design"),
    ("finance.om_share_of_capex", "design", "Annual O&M", "share of capex", G_PRICE, 0, 0.15, "design"),
    ("network.tariff_share_of_current_cost", "design", "Tariff vs. buildings' current heat cost", "share", G_PRICE, 0.2, 1.5, "design"),
    ("fuels.unknown.usd_per_mmbtu", "design", "Default heat cost (fuel unknown)", "$/MMBtu", G_PRICE, 1, 200, "design"),
    ("fuels.gas.usd_per_mmbtu", "design", "Gas heat cost", "$/MMBtu", G_PRICE, 1, 200, "design"),

    ("methods.hot_water.pipe_usd_per_m", "design", "Hot-water loop pipe", "$/m", G_METHOD, 50, 20000, "design"),
    ("methods.ambient.pipe_usd_per_m", "design", "Ambient loop pipe", "$/m", G_METHOD, 50, 20000, "design"),
    ("methods.direct.pipe_usd_per_m", "design", "Direct warm loop pipe", "$/m", G_METHOD, 50, 20000, "design"),
    ("methods.hot_water.station_usd_per_kw", "design", "Building substation (hot-water)", "$/kW", G_METHOD, 0, 2000, "design"),
    ("methods.direct.station_usd_per_kw", "design", "Substation + retrofit (direct)", "$/kW", G_METHOD, 0, 2000, "design"),
    ("methods.hot_water.hp_usd_per_kw", "design", "Heat pump at the site", "$/kW", G_METHOD, 0, 5000, "design"),
    ("methods.ambient.hp_usd_per_kw", "design", "Heat pump in each building", "$/kW", G_METHOD, 0, 5000, "design"),
    ("heat_pump.site_cop", "design", "Site heat pump COP (55 -> 70 C)", "COP", G_METHOD, 1.5, 12, "design"),
    ("network.detour_factor", "design", "Route detour over straight line", "x", G_METHOD, 1, 3, "design"),
    ("network.street_cost_share", "design", "Street/service pipe vs. trunk cost", "share", G_METHOD, 0, 2, "design"),

    ("anchors.heat_price_usd_per_mwh", "design", "Heat price to on-site users", "$/MWh", G_ANCHOR, 0, 500, "design"),
    ("anchors.station_usd_per_kw", "design", "On-site connection station", "$/kW", G_ANCHOR, 0, 2000, "design"),
    ("anchors.greenhouse.max_mw", "design", "Greenhouse size cap", "MW", G_ANCHOR, 0, 500, "design"),
    ("anchors.greenhouse.peak_mw_per_acre", "design", "Greenhouse peak load per acre", "MW/acre", G_ANCHOR, 0.05, 3, "design"),
    ("anchors.aquaculture.max_mw", "design", "Aquaculture size cap", "MW", G_ANCHOR, 0, 200, "design"),
    ("anchors.drying.max_mw", "design", "Drying size cap", "MW", G_ANCHOR, 0, 200, "design"),

    ("methods.trucked.usd_per_mwh_fixed", "design", "Container stations + rental", "$/MWh", G_TRUCK, 0, 500, "design"),
    ("methods.trucked.usd_per_mwh_km", "design", "Haulage", "$/MWh/km", G_TRUCK, 0, 50, "design"),
    ("methods.trucked.mwh_per_trip", "design", "Heat per container trip", "MWh", G_TRUCK, 0.1, 20, "design"),
    ("methods.trucked.max_trips_per_day", "design", "Fleet limit", "trips/day", G_TRUCK, 0, 1000, "design"),
    ("methods.trucked.max_road_km", "design", "Longest worthwhile road trip", "km", G_TRUCK, 1, 300, "design"),
    ("methods.trucked.min_mwh_per_year", "design", "Smallest user worth a schedule", "MWh/yr", G_TRUCK, 0, 100000, "design"),

    ("storage.capacity_mwh", "site", "Pit capacity", "MWh", G_STORE, 0, 100000, "simulation"),
    ("storage.max_charge_mw", "site", "Pit max charge", "MW", G_STORE, 0, 1000, "simulation"),
    ("storage.max_discharge_mw", "site", "Pit max discharge", "MW", G_STORE, 0, 1000, "both"),
    ("storage.loss_per_hour", "site", "Pit loss", "share/h", G_STORE, 0, 0.01, "simulation"),
]
SPEC = {s[0]: dict(zip(("key", "file", "label", "unit", "group", "min", "max", "affects"), s)) for s in SPECS}


# ------------------------------------------------------------------------------- yaml comments

def parse_comments(text: str) -> dict[str, str]:
    """{dotted.path: inline comment} for a block-style YAML file. A comment on a flow mapping
    (`gas: {a: 1, b: 2}  # note`) is stored on the mapping's path; children fall back to it."""
    out: dict[str, str] = {}
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = re.match(r"^(\s*)([A-Za-z0-9_]+):(.*)$", line)
        if not m:
            continue
        indent, key, rest = len(m.group(1)), m.group(2), m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        if "#" in rest:
            val, com = rest.split("#", 1)
            if com.strip():
                out[path] = com.strip()
        stack.append((indent, key))
    return out


@lru_cache(maxsize=4)
def _comments(file: str) -> dict[str, str]:
    return parse_comments((ROOT / "config" / f"{file}.yaml").read_text())


def source_of(key: str, file: str, site_cfg: dict | None = None) -> str:
    com = _comments(file)
    parts = key.split(".")
    note = next((com[".".join(parts[:i])] for i in range(len(parts), 0, -1) if ".".join(parts[:i]) in com), "")
    if file == "site" and site_cfg and isinstance(site_cfg.get("notes"), dict):
        extra = site_cfg["notes"].get(parts[0])
        if extra:
            note = f"{note} | this site: {extra}" if note else f"this site: {extra}"
    return note


# --------------------------------------------------------------------------------- overrides

OVERRIDES_DIR: Path | None = None    # tests set this; default DATA_DIR/outputs/overrides (paths.py)


def overrides_dir() -> Path:
    if OVERRIDES_DIR is not None:
        return Path(OVERRIDES_DIR)
    try:
        import paths
        return paths.OUTPUTS_DIR / "overrides"
    except ImportError:
        return ROOT / "outputs" / "overrides"


def overrides_path(site: str) -> Path:
    """outputs/overrides/<id>.yaml under DATA_DIR: writable on a deploy, and never inside a site's own
    directory (the shipped sites/ copies stay read-only)."""
    import sites
    return overrides_dir() / f"{sites.check_id(site)}.yaml"


def _read(site: str) -> dict:
    try:
        doc = yaml.safe_load(overrides_path(site).read_text()) or {}
    except FileNotFoundError:
        return {}
    return doc if isinstance(doc, dict) else {}


def load_overrides(site: str | None) -> dict:
    """{key: value} for the site's known keys (unknown keys in the file are ignored)."""
    try:
        doc = _read(site or "lansing")
    except ValueError:
        return {}
    return {k: v for k, v in (doc.get("overrides") or {}).items() if k in SPEC}


def _get(d: dict, key: str):
    for p in key.split("."):
        d = d[p]
    return d


def _set(d: dict, key: str, v) -> None:
    parts = key.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = v


def apply(cfg: dict, file: str, site: str | None) -> dict:
    """A copy of cfg (site.yaml or design.yaml contents) with this site's overrides for that file applied."""
    ov = {k: v for k, v in load_overrides(site).items() if SPEC[k]["file"] == file}
    if not ov:
        return cfg
    cfg = copy.deepcopy(cfg)
    for k, v in ov.items():
        _set(cfg, k, v)
    return cfg


def _raw_cfgs(site: str) -> tuple[dict, dict]:
    import sites
    s = yaml.safe_load(sites.paths(sites.check_id(site))["site"].read_text())
    d = yaml.safe_load((ROOT / "config" / "design.yaml").read_text())
    return s, d


def supply_scale(site: str | None) -> tuple[float, float]:
    """(IT ratio, captured-heat ratio) when IT MW or capture fraction are overridden; (1, 1) otherwise.
    supply.csv was made for the site's own IT MW and capture fraction, so it is scaled by these."""
    ov = load_overrides(site)
    if "data_center.it_load_mw" not in ov and "data_center.capture_fraction" not in ov:
        return 1.0, 1.0
    s, _ = _raw_cfgs(site or "lansing")
    dc = s["data_center"]
    it = float(ov.get("data_center.it_load_mw", dc["it_load_mw"])) / float(dc["it_load_mw"])
    cap = float(ov.get("data_center.capture_fraction", dc["capture_fraction"])) / float(dc["capture_fraction"])
    return it, it * cap


def registry(site: str | None = None) -> list[dict]:
    site = site or "lansing"
    s, d = _raw_cfgs(site)
    ov = load_overrides(site)
    out = []
    for key, spec in SPEC.items():
        cfg = s if spec["file"] == "site" else d
        try:
            default = _get(cfg, key)
        except (KeyError, TypeError):
            continue
        src = source_of(key, spec["file"], s if site != "lansing" else None)
        out.append({**spec, "default": default, "value": ov.get(key, default), "overridden": key in ov,
                    "source": src, "verify": bool(VERIFY_RE.search(src)),
                    "file_path": "config/site.yaml" if spec["file"] == "site" and site == "lansing"
                    else f"sites/{site}/site.yaml" if spec["file"] == "site" else "config/design.yaml"})
    return out


def grouped(site: str | None = None) -> dict:
    items = registry(site)
    doc = {}
    try:
        doc = _read(site or "lansing")
    except ValueError:
        pass
    return {"site": site or "lansing", "groups": [{"group": g, "items": [i for i in items if i["group"] == g]}
                                                  for g in GROUPS],
            "overridden": sum(i["overridden"] for i in items), "updated": doc.get("updated"),
            "history": (doc.get("history") or [])[-20:]}


def validate(site: str | None, values: dict) -> dict:
    """Numeric, finite, within [min, max]; None means "back to default". Raises ValueError listing every problem."""
    if not isinstance(values, dict) or not values:
        raise ValueError("give {key: value, ...}")
    defaults = {i["key"]: i["default"] for i in registry(site)}
    errs, clean = [], {}
    for k, v in values.items():
        if k not in SPEC or k not in defaults:
            errs.append(f"{k}: unknown assumption")
            continue
        if v is None:
            clean[k] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            errs.append(f"{k}: must be a number")
            continue
        try:
            x = float(v)
        except ValueError:
            errs.append(f"{k}: must be a number")
            continue
        sp = SPEC[k]
        if not math.isfinite(x):
            errs.append(f"{k}: must be finite")
        elif not sp["min"] <= x <= sp["max"]:
            errs.append(f"{k}: {x:g} is outside {sp['min']:g}-{sp['max']:g} {sp['unit']}")
        else:
            clean[k] = int(round(x)) if isinstance(defaults[k], int) and not isinstance(defaults[k], bool) else x
    if errs:
        raise ValueError("; ".join(errs))
    return clean


def _clear_caches() -> None:
    import design
    design._cfgs_for.cache_clear()


def save(site: str | None, values: dict) -> dict:
    """Validate and merge into the site's overrides (a value equal to the default, or None, removes it).
    Returns the new {key: value} overrides."""
    site = site or "lansing"
    clean = validate(site, values)
    defaults = {i["key"]: i["default"] for i in registry(site)}
    with _lock:
        doc = _read(site)
        ov = {k: v for k, v in (doc.get("overrides") or {}).items() if k in SPEC}
        hist = list(doc.get("history") or [])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for k, v in clean.items():
            before = ov.get(k, defaults[k])
            if v is None or v == defaults[k]:
                ov.pop(k, None)
                v = defaults[k]
            else:
                ov[k] = v
            if before != v:
                hist.append({"at": now, "key": k, "from": before, "to": v})
        _write(site, {"overrides": ov, "updated": now, "history": hist[-200:]})
    _clear_caches()
    return ov


def reset(site: str | None) -> None:
    site = site or "lansing"
    with _lock:
        doc = _read(site)
        hist = list(doc.get("history") or [])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if doc.get("overrides"):
            hist.append({"at": now, "key": "*", "from": "overrides", "to": "defaults"})
        _write(site, {"overrides": {}, "updated": now, "history": hist[-200:]})
    _clear_caches()


def _write(site: str, doc: dict) -> None:
    text = ("# Alpine assumption overrides for this site (written by the Assumptions tab / API).\n"
            + yaml.safe_dump(doc, sort_keys=False))
    p = overrides_path(site)
    try:
        import paths
        paths.write_text_atomic(p, text)
    except ImportError:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


# -------------------------------------------------------------------------------- provenance

FIN = ["finance.discount_rate", "finance.horizon_years", "finance.om_share_of_capex"]
PATHWAY_KEYS = {
    "network": ["methods.hot_water.pipe_usd_per_m", "methods.ambient.pipe_usd_per_m", "methods.direct.pipe_usd_per_m",
                "methods.hot_water.station_usd_per_kw", "methods.direct.station_usd_per_kw",
                "methods.hot_water.hp_usd_per_kw", "methods.ambient.hp_usd_per_kw", "heat_pump.site_cop",
                "network.detour_factor", "network.street_cost_share", "network.tariff_share_of_current_cost",
                "fuels.unknown.usd_per_mmbtu", "fuels.gas.usd_per_mmbtu", "prices.electricity_usd_per_mwh",
                "emissions.grid_t_per_mwh", *FIN, "finance.carbon_price_usd_per_t"],
    "greenhouse": ["anchors.heat_price_usd_per_mwh", "anchors.station_usd_per_kw", "anchors.greenhouse.max_mw",
                   "anchors.greenhouse.peak_mw_per_acre", "prices.electricity_usd_per_mwh", "emissions.grid_t_per_mwh",
                   *FIN, "finance.carbon_price_usd_per_t"],
    "aquaculture": ["anchors.heat_price_usd_per_mwh", "anchors.station_usd_per_kw", "anchors.aquaculture.max_mw",
                    "prices.electricity_usd_per_mwh", "emissions.grid_t_per_mwh", *FIN, "finance.carbon_price_usd_per_t"],
    "drying": ["anchors.heat_price_usd_per_mwh", "anchors.station_usd_per_kw", "anchors.drying.max_mw",
               "prices.electricity_usd_per_mwh", "emissions.grid_t_per_mwh", *FIN, "finance.carbon_price_usd_per_t"],
    "trucked": ["methods.trucked.usd_per_mwh_fixed", "methods.trucked.usd_per_mwh_km", "methods.trucked.mwh_per_trip",
                "methods.trucked.max_trips_per_day", "methods.trucked.max_road_km", "methods.trucked.min_mwh_per_year",
                "network.tariff_share_of_current_cost", "network.detour_factor", "fuels.unknown.usd_per_mmbtu",
                "fuels.gas.usd_per_mmbtu", "finance.discount_rate", "finance.horizon_years",
                "finance.carbon_price_usd_per_t"],
}
PATHWAY_DATA = {"network": ["buildings", "demand", "method"], "greenhouse": ["supply", "weather", "method"],
                "aquaculture": ["supply", "method"], "drying": ["supply", "method"],
                "trucked": ["buildings", "demand", "method"]}


def data_sources(site: str) -> dict[str, str]:
    """Plain-language data sources for the site, keyed by kind."""
    import sites
    meta = sites.read_meta(site) or {}
    if site == "lansing":
        return {"supply": "Supply model output, 100 MW AI inference (data/supply.csv: hourly IT load, captured heat, COP)",
                "demand": "Quantile demand model on HeatOS Lansing parcel data (data/demand.csv, data/customers.csv: "
                          "4,226 buildings within 15 km, annual heat and current cost)",
                "buildings": "HeatOS Lansing parcel pipeline (names and addresses removed, homes rounded to ~100 m)",
                "weather": "NOAA Ithaca hourly temperature (in data/demand.csv)",
                "method": "Alpine design model (design.py): clustered network search, hourly anchor sizing, trucked logistics"}
    src = meta.get("sources") or {}
    fetched = (meta.get("finished") or meta.get("created") or "")[:10]
    raw = sites.paths(site).get("raw")
    try:
        f = next(iter(sorted(raw.glob("overpass_bldg_geom_*.json"))), None) if raw else None
        if f:
            fetched = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        pass
    it = meta.get("it_mw") or 100
    return {"supply": f"Lansing supply model scaled by IT MW/100 ({it:g} MW / 100)",
            "demand": "ESTIMATED demand (footprint x levels x heat intensity by building type, scaled by heating "
                      "degree-days; hourly shape from temperature)",
            "buildings": f"OpenStreetMap buildings (Overpass, fetched {fetched or 'at build'})"
                         + (f": {src['buildings']}" if src.get("buildings") else ""),
            "weather": "Open-Meteo 2023 hourly temperature (archive API)",
            "geocode": src.get("geocode", "entered lat/lon"),
            "method": "Alpine design model (design.py): clustered network search, hourly anchor sizing, trucked logistics"}


def keewano_ref(site: str) -> dict | None:
    """The site's latest normal-year run in Keewano (local index only) and the queries behind PUE/WUE/ERF."""
    try:
        import agent
        import keewano
        rid = agent.latest_run("normal", sim="alpine", site=site)
        if not rid:
            import sites
            rid = (sites.read_meta(site) or {}).get("run_id")
        if not rid:
            return None
        return {"run_id": rid, "entity": f"uuid5(\"{site}:{rid}\")", "queries": keewano.report_queries(rid)}
    except Exception:  # noqa: BLE001 - provenance never breaks the plan
        return None


def provenance(site: str | None, rec: dict) -> dict:
    site = site or "lansing"
    reg = {i["key"]: i for i in registry(site)}

    def inp(keys):
        return [{k: reg[key][k] for k in ("key", "label", "value", "unit", "default", "overridden", "source", "verify")}
                for key in dict.fromkeys(keys) if key in reg]

    ds = data_sources(site)
    kw = keewano_ref(site)
    rec_keys = [p["key"] for p in rec.get("pathways", []) if p.get("verdict") == "recommend"]
    union = [k for pk in rec_keys for k in PATHWAY_KEYS.get(pk, [])]
    supply_keys = ["data_center.it_load_mw", "data_center.capture_fraction"]
    out = {
        "summary": {
            "heat_available_gwh": {"inputs": inp(supply_keys), "data": [ds["supply"]],
                                   "how": "sum over 8,760 hours of captured heat (P50) in supply.csv, scaled if IT MW "
                                          "or capture fraction is overridden"},
            "share_reused": {"inputs": inp(supply_keys + union), "data": [ds["supply"], ds["demand"]],
                             "how": "hour by hour: network draw + on-site users (capped at captured heat), plus "
                                    "trucked heat capped at what is left, divided by heat available"},
            "npv_recommended_usd": {"inputs": inp(union + FIN), "data": [ds["demand"], ds["method"]],
                                    "how": "sum of the 20-year NPVs of the pathways marked Recommend: "
                                           + (", ".join(rec_keys) or "none")},
            "co2_avoided_t": {"inputs": inp(["emissions.grid_t_per_mwh", "fuels.unknown.usd_per_mmbtu"] + union),
                              "data": [ds["demand"]],
                              "how": "displaced boiler fuel CO2 (design.yaml fuels: t/MWh / boiler efficiency) minus "
                                     "grid CO2 of the added heat pump and pump electricity, recommended pathways"},
        },
        "pathways": {pk: {"inputs": inp(keys), "data": [ds[d] for d in PATHWAY_DATA[pk] if d in ds]}
                     for pk, keys in PATHWAY_KEYS.items()},
        "data_sources": ds,
        "keewano": kw,
        "overrides": load_overrides(site),
    }
    if kw:
        out["simulation"] = {"run_id": kw["run_id"],
                             "note": "PUE / WUE / ERF on the Report tab are Keewano SQL over this run's events",
                             "queries": kw["queries"]}
    return out
