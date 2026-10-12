"""Server-side exports of a site's plan: printable HTML report, JSON bundle, CSVs.

    plan_bundle(site, rec, meta) -> dict            (GET /sites/{id}/plan.json)
    plan_html(site, rec, meta)   -> str             (GET /sites/{id}/plan.html)
    pathways_csv(rec) / assumptions_csv(site) -> str
"""
from __future__ import annotations

import csv
import io
import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
from pathlib import Path

import assumptions

ROOT = Path(__file__).resolve().parent
VERSION = "0.2"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
VLABEL = {"recommend": "Recommend", "only with a carbon price": "Only with a carbon price",
          "not recommended": "Not recommended"}


@lru_cache(maxsize=1)
def commit() -> str | None:
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT")
    if sha:
        return sha[:7]
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             timeout=3)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def filename(site: str, ext: str, kind: str = "plan") -> str:
    return f"alpine-{kind}-{site}-{today()}.{ext}"


def plan_bundle(site: str, rec: dict, meta: dict | None) -> dict:
    return {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generator": {"name": "Alpine", "version": VERSION, "commit": commit()},
            "site": site, "meta": meta, "design": rec, "assumptions": assumptions.registry(site),
            "provenance": rec.get("provenance")}


def _csv(rows: list[dict], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def pathways_csv(rec: dict) -> str:
    prov = (rec.get("provenance") or {}).get("pathways", {})
    rows = []
    for p in rec.get("pathways", []):
        ins = prov.get(p["key"], {}).get("inputs", [])
        rows.append({**p, "gwh_per_yr": round((p.get("delivered_mwh") or 0) / 1000, 3),
                     "overridden_inputs": "; ".join(f"{i['key']}={i['value']}" for i in ins if i["overridden"]),
                     "verify_inputs": sum(1 for i in ins if i["verify"])})
    return _csv(rows, ["key", "label", "verdict", "gwh_per_yr", "delivered_mwh", "peak_mw", "capex_usd", "npv_usd",
                       "npv_with_carbon_usd", "co2_avoided_t", "detail", "overridden_inputs", "verify_inputs"])


def assumptions_csv(site: str) -> str:
    return _csv(assumptions.registry(site), ["group", "key", "label", "value", "unit", "default", "overridden",
                                             "min", "max", "verify", "affects", "file_path", "source"])


# ---------------------------------------------------------------------------------- html

def _usd(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    s = f"${a / 1e6:,.1f}M" if a >= 1e6 else f"${a / 1e3:,.0f}k" if a >= 1e3 else f"${a:,.0f}"
    return ("−" if v < 0 else "") + s


def _n(v, d: int = 0) -> str:
    return "—" if v is None else f"{v:,.{d}f}"


def _val(v) -> str:
    if isinstance(v, float):
        return f"{v:,.4g}" if abs(v) < 1000 else f"{v:,.0f}"
    return escape(str(v))


CSS = """
  :root { --ink:#171717; --muted:#686868; --border:#e8e8e8; --green:#377a53; --green-fill:#cee7d7; --amber:#a8742c;
          --amber-fill:#f3e4cc; --red:#b33f3f; --red-fill:#f0d6d6; --gray-fill:#eef0ef; }
  * { box-sizing:border-box; }
  body { font:13.5px/1.55 Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:var(--ink);
         max-width:920px; margin:0 auto; padding:40px 32px 60px; background:#fff; -webkit-font-smoothing:antialiased; }
  .cover { border-bottom:1px solid var(--border); padding-bottom:24px; margin-bottom:8px; }
  .brand { display:flex; align-items:center; gap:8px; color:var(--muted); font-size:12px; letter-spacing:.2px; }
  .brand svg { width:18px; height:18px; }
  h1 { font-size:28px; font-weight:600; letter-spacing:-.6px; margin:18px 0 6px; line-height:1.2; }
  .sub { color:var(--muted); font-size:13px; }
  .facts { display:flex; flex-wrap:wrap; gap:6px 22px; margin-top:14px; font-size:12.5px; }
  .facts b { font-weight:500; }
  h2 { font-size:16px; font-weight:600; letter-spacing:-.2px; margin:32px 0 10px; }
  h3 { font-size:13.5px; font-weight:600; margin:18px 0 6px; }
  p { margin:6px 0; }
  .muted { color:var(--muted); }
  .stats { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:14px 0; }
  .stats div { border:1px solid var(--border); border-radius:10px; padding:12px 14px; }
  .stats span { color:var(--muted); font-size:11.5px; display:block; }
  .stats b { display:block; font-size:22px; font-weight:600; letter-spacing:-.5px; margin-top:2px; }
  .lead { font-size:14.5px; line-height:1.6; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); vertical-align:top; }
  th { color:var(--muted); font-weight:500; font-size:11.5px; }
  .n { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
  .pill { display:inline-block; padding:1px 7px; border-radius:99px; font-size:10.5px; font-weight:500; white-space:nowrap;
          background:var(--gray-fill); color:var(--muted); }
  .pill.rec { background:var(--green-fill); color:var(--green); }
  .pill.carbon, .pill.verify { background:var(--amber-fill); color:var(--amber); }
  .pill.no { background:var(--red-fill); color:var(--red); }
  .pill.ov { background:#171717; color:#fff; }
  tr.ov td { background:#fafaf5; }
  .src { color:var(--muted); font-size:11.5px; }
  ol, ul { padding-left:20px; } li { margin:4px 0; }
  .box { border:1px solid var(--border); border-radius:10px; padding:12px 16px; margin:10px 0; }
  code { font:11px ui-monospace, SFMono-Regular, Menlo, monospace; background:#fafafa; border:1px solid var(--border);
         border-radius:5px; padding:1px 5px; word-break:break-all; }
  .foot { margin-top:40px; color:var(--muted); font-size:11.5px; border-top:1px solid var(--border); padding-top:12px; }
  .noprint { margin:0 0 18px; }
  .noprint button { font:inherit; font-size:12.5px; height:32px; padding:0 14px; border-radius:7px; border:1px solid #171717;
                    background:#171717; color:#fff; cursor:pointer; }
  @page { margin:16mm 14mm; }
  @media print {
    body { padding:0; max-width:none; font-size:11.5px; }
    .noprint { display:none; }
    .cover { min-height:auto; }
    h2 { break-after:avoid; } table, .stats, .box { break-inside:avoid; }
    .pagebreak { break-before:page; }
    tr.ov td { background:none; }
  }
  @media (max-width:640px) { body { padding:24px 16px; } .stats { grid-template-columns:repeat(2,1fr); } }
"""


def plan_html(site: str, rec: dict, meta: dict | None) -> str:
    meta = meta or {}
    e = escape
    s, P = rec.get("summary", {}), rec.get("pathways", [])
    net, be = rec.get("network", {}), rec.get("network_breakeven", {})
    nb = net.get("best_candidate") or {}
    prov = rec.get("provenance") or {}
    reg = assumptions.registry(site)
    loc = rec.get("location", {})
    name = "Lansing, NY (worked example)" if site == "lansing" else meta.get("name") or rec.get("site") or site
    n_ov = sum(i["overridden"] for i in reg)
    n_ver = sum(i["verify"] for i in reg)
    recs = rec.get("recommendation", [])
    lead, rest = (recs[0] if recs else ""), recs[1:]

    def pill(v):
        k = "rec" if v == "recommend" else "carbon" if v == "only with a carbon price" else "no"
        return f'<span class="pill {k}">{e(VLABEL.get(v, v))}</span>'

    pw_rows = "".join(
        f"<tr><td><b>{e(p['label'])}</b><div class='src'>{e(p.get('detail', ''))}</div></td><td>{pill(p['verdict'])}</td>"
        f"<td class='n'>{_n((p.get('delivered_mwh') or 0) / 1000, 1)}</td>"
        f"<td class='n'>{'—' if p.get('peak_mw') is None else _n(p['peak_mw'], 1)}</td>"
        f"<td class='n'>{_usd(p.get('capex_usd'))}</td><td class='n'>{_usd(p.get('npv_usd'))}</td>"
        f"<td class='n'>{_usd(p.get('npv_with_carbon_usd'))}</td><td class='n'>{_n(p.get('co2_avoided_t'))}</td></tr>"
        for p in P)

    months = rec.get("hourly", {}).get("months", [])
    keys = [k for k in ("network", "greenhouse", "aquaculture", "drying") if any(m.get(k, 0) > 0 for m in months)]
    labels = {"network": "Network", "greenhouse": "Greenhouse", "aquaculture": "Aquaculture", "drying": "Drying"}
    m_rows, tot = "", {k: 0.0 for k in ["avail", *keys, "used", "left"]}
    for m in months:
        used = sum(m.get(k, 0) for k in keys)
        left = max(m.get("avail", 0) - used, 0)
        for k, v in (("avail", m.get("avail", 0)), *((k, m.get(k, 0)) for k in keys), ("used", used), ("left", left)):
            tot[k] += v
        m_rows += (f"<tr><td>{MONTHS[(m.get('month') or 1) - 1]}</td><td class='n'>{_n(m.get('avail'), 1)}</td>"
                   + "".join(f"<td class='n'>{_n(m.get(k), 1)}</td>" for k in keys)
                   + f"<td class='n'>{_n(used, 1)}</td><td class='n'>{_n(left, 1)}</td></tr>")
    m_rows += (f"<tr><td><b>Year</b></td><td class='n'><b>{_n(tot['avail'], 0)}</b></td>"
               + "".join(f"<td class='n'><b>{_n(tot[k], 0)}</b></td>" for k in keys)
               + f"<td class='n'><b>{_n(tot['used'], 0)}</b></td><td class='n'><b>{_n(tot['left'], 0)}</b></td></tr>")
    m_head = ("<tr><th>Month</th><th class='n'>Available</th>" + "".join(f"<th class='n'>{labels[k]}</th>" for k in keys)
              + "<th class='n'>Reused on site + network</th><th class='n'>Left (trucked / rejected)</th></tr>")

    net_pays = net.get("connected")
    verdict = ("A district network pays" if net_pays else
               "A district network pays only with a carbon price" if be.get("pays_with_carbon_price") else
               "No district network pays")
    share = be.get("pipe_cost_breakeven_share")

    a_rows = ""
    for g in assumptions.GROUPS:
        items = [i for i in reg if i["group"] == g]
        if not items:
            continue
        a_rows += f"<tr><td colspan='5' style='padding-top:14px'><b>{e(g)}</b></td></tr>"
        for i in items:
            flags = ("<span class='pill ov'>overridden</span> " if i["overridden"] else "") + \
                    ("<span class='pill verify'>verify</span>" if i["verify"] else "")
            a_rows += (f"<tr class='{'ov' if i['overridden'] else ''}'><td>{e(i['label'])}<div class='src'>"
                       f"<code>{e(i['key'])}</code></div></td>"
                       f"<td class='n'>{_val(i['value'])} <span class='muted'>{e(i['unit'])}</span></td>"
                       f"<td class='n muted'>{_val(i['default'])}</td><td>{flags}</td>"
                       f"<td class='src'>{e(i['source'] or 'no source note')}</td></tr>")

    ds = prov.get("data_sources", {})
    ds_rows = "".join(f"<li><b>{e(k.capitalize())}</b>: {e(v)}</li>" for k, v in ds.items())
    notes = meta.get("notes") or []
    est = "".join(f"<li>{e(n)}</li>" for n in notes)
    kw = prov.get("keewano")
    kw_html = ""
    if kw:
        q = "".join(f"<li><b>{e(k)}</b>: <code>{e(v)}</code></li>" for k, v in (kw.get("queries") or {}).items())
        kw_html = (f"<h3>Simulation and Keewano</h3><p>Normal-year simulation run <code>{e(kw['run_id'])}</code>, "
                   f"streamed to Keewano as entity <code>{e(kw['entity'])}</code>. PUE, WUE and ERF on the Report tab "
                   f"are these Keewano queries:</p><ul>{q}</ul>")

    be_text = (f"it pays if pipe cost falls to <b>{share * 100:.0f}%</b> of today's." if share is not None and share < 1
               else "it pays at today's pipe cost." if share else
               "no pipe-cost reduction tested (down to 5%) makes it pay.")
    sha = commit()
    gen = f"Alpine {VERSION}" + (f" · commit {sha}" if sha else "")
    reuse_pct = (s.get("share_reused") or 0) * 100
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Heat-reuse plan: {e(name)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<div class="noprint"><button onclick="window.print()">Print / save as PDF</button></div>
<section class="cover">
  <div class="brand"><svg viewBox="0 0 24 24" fill="none" stroke="#377a53" stroke-width="1.8" stroke-linejoin="round"><path d="m8 3 4 8 5-5 5 15H2L8 3z"/></svg>Alpine · Heat-reuse plan</div>
  <h1>{e(name)}</h1>
  <div class="sub">{e(meta.get('display_name') or '')}</div>
  <div class="facts"><span><b>Location</b> {_n(loc.get('lat'), 4)}, {_n(loc.get('lon'), 4)}</span>
    <span><b>IT load</b> {_n(loc.get('it_mw'))} MW</span><span><b>Buildings in radius</b> {_n(s.get('buildings_within_15km'))} within {_n(s.get('radius_km'))} km</span>
    <span><b>Date</b> {today()}</span><span><b>Assumptions</b> {len(reg)} ({n_ov} overridden, {n_ver} marked verify)</span></div>
</section>

<h2>Executive summary</h2>
<div class="stats">
  <div><span>Heat available</span><b>{_n(s.get('heat_available_gwh'))} GWh/yr</b></div>
  <div><span>Share reused</span><b>{reuse_pct:.0f}%</b></div>
  <div><span>20-year NPV (recommended)</span><b>{_usd(s.get('npv_recommended_usd'))}</b></div>
  <div><span>CO2 avoided</span><b>{_n(s.get('co2_avoided_t'))} t/yr</b></div>
</div>
<p class="lead">{e(lead)}</p>
{'<p class="muted">Heat demand at this site is <b>estimated</b> from OpenStreetMap buildings, not metered.</p>' if s.get('demand_estimated') else ''}
{f'<p class="muted">This plan uses {n_ov} overridden assumption{"s" if n_ov != 1 else ""} (marked in the assumptions table).</p>' if n_ov else ''}

<h2>Recommendation</h2>
<ol>{''.join(f"<li>{e(l.split('. ', 1)[1] if l[:1].isdigit() and '. ' in l else l)}</li>" for l in rest)}</ol>

<h2>Pathways</h2>
<p class="muted">All options on the same terms: 20-year NPV at {_val(assumptions_value(reg, 'finance.discount_rate'))} discount, CO2 avoided, heat delivered. Ranked by NPV.</p>
<table><thead><tr><th>Pathway</th><th>Verdict</th><th class="n">GWh/yr</th><th class="n">Peak MW</th><th class="n">Capex</th><th class="n">NPV</th><th class="n">NPV with carbon</th><th class="n">CO2 t/yr</th></tr></thead>
<tbody>{pw_rows}</tbody></table>

<h2>Monthly heat balance</h2>
<p class="muted">GWh per month. Network and on-site users are checked hour by hour against captured heat.</p>
<table><thead>{m_head}</thead><tbody>{m_rows}</tbody></table>

<h2>District network</h2>
<div class="box"><p><b>{verdict}.</b> Best network found: {_n(nb.get('buildings'))} buildings, {_n(nb.get('pipe_km'), 1)} km of pipe,
{_n(nb.get('delivered_mwh'))} MWh/yr, capex {_usd(nb.get('capex_usd'))}, NPV {_usd(nb.get('npv_usd'))}
(with carbon {_usd(nb.get('npv_with_carbon_usd'))}).</p>
<p>Breakeven: {be_text}
{'It pays at the carbon price.' if be.get('pays_with_carbon_price') else 'It does not pay at the carbon price.'}
Linear heat density {_n(rec.get('linear_heat_density_mwh_per_m'), 2)} MWh per metre if every cluster were piped (district heat needs ~1.5).</p>
<p>Storage pit: <b>{e(rec.get('pit', {}).get('recommend', '—'))}</b>. {e(rec.get('pit', {}).get('why', ''))}</p></div>

<h2 class="pagebreak">Assumptions</h2>
<p class="muted">Every input that moves the plan, with its value, default and source. <span class="pill verify">verify</span> = the source says "assumption - verify": a placeholder to replace with a quote or a meter.</p>
<table><thead><tr><th>Assumption</th><th class="n">Value</th><th class="n">Default</th><th>Flags</th><th>Source</th></tr></thead><tbody>{a_rows}</tbody></table>

<h2>Data sources and what is estimated</h2>
<ul>{ds_rows}</ul>
{f'<h3>Estimated</h3><ul>{est}</ul>' if est else ''}
{kw_html}

<h2>Methodology</h2>
<ul>
<li><b>District network.</b> Buildings are grouped into ~750 m clusters and a network is grown outward from the site, adding the cluster whose connection pays best given the pipe to the nearest point already on the network. For each cluster the cheapest of three methods is chosen: hot-water loop (heat pump at the site), ambient loop (heat pump in each building) or direct warm loop (low-temperature buildings only). The largest clusters are each tried as the far end of the first trunk; every pipe is sized for everything downstream of it and the best prefix of the tree is kept.</li>
<li><b>On-site users</b> (greenhouse, aquaculture, drying) take 55 C heat directly. Each is sized as the largest peak that fits under the heat left every hour, up to its market cap.</li>
<li><b>Trucked thermal storage</b> serves large off-network users whose value beats haulage cost, best margin first, within the container fleet limit and the heat that is left.</li>
<li><b>Economics.</b> NPV = annual net value x annuity factor - capex x (1 + O&M share x annuity factor). Buildings pay a tariff as a share of their current heat cost; CO2 is displaced boiler fuel minus grid CO2 of added electricity. The "with carbon" NPV adds avoided CO2 at the carbon price.</li>
<li><b>Hourly simulation</b> (Report tab) runs the normal year hour by hour: sell, discharge, store, reject; streamed to Keewano, where PUE / WUE / ERF are computed by SQL.</li>
<li>This is a screening study, not an engineering design. Replace every "verify" assumption with a quote or a meter before committing capital.</li>
</ul>

<div class="foot">Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by {e(gen)} · site <code>{e(site)}</code>{' · computed ' + e(rec.get('computed_at', '')) if rec.get('computed_at') else ''}</div>
</body></html>"""


def assumptions_value(reg: list[dict], key: str):
    v = next((i["value"] for i in reg if i["key"] == key), None)
    return f"{v:.0%}" if isinstance(v, float) and v < 1 else v
