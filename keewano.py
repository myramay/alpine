"""Keewano I/O for Thermal Layer: ingest (REST), read (SQL over the Postgres wire, MCP).

Writes use KEEWANO_API_KEY (project SDK key). Reads use KEEWANO_PAT (kwpat-...):
MCP at KEEWANO_MCP_URL, SQL at pgsql.keewano.com:27543 as KEEWANO_EMAIL / dbname
KEEWANO_PROJECT_ID. Without KEEWANO_API_KEY every send is a dry run (counts only).

CLI:
  python keewano.py check | ping | tools | sql "<query>" | report <run_id>
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import httpx

import schema

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:  # pragma: no cover
    pass

INGEST_URL = (os.environ.get("KEEWANO_INGEST_URL") or "https://api.keewano.com/event/ingress/v1/json").rstrip("/")
if INGEST_URL.endswith("/in"):  # accept the full /in endpoint too
    INGEST_URL = INGEST_URL[:-3]
DEFAULT_MCP_URL = "https://api.keewano.com/mcp"
SQL_HOST, SQL_PORT = "pgsql.keewano.com", 27543
SDK_VERSION = "thermal-layer/0.1"
MAX_RETRIES = 6
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RUN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(-[0-9a-f]{2,12}){1,2}$")  # normal-7f3a9c, normal-10112130-ab12

_sleep: Callable[[float], None] = time.sleep  # tests patch this to skip backoff
_registered: str | None = None  # one registration per process


def _log(msg: str) -> None:
    print(f"[keewano] {msg}", flush=True)


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _client(api_key: str) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"k-token": api_key, "Content-Type": "application/json"})


class BatchTooLarge(Exception):
    pass


# --------------------------------------------------------------------------- ingest

def _too_large(resp: httpx.Response) -> bool:
    if resp.status_code == 413:
        return True
    if resp.status_code == 400:
        body = resp.text.lower()
        return any(w in body for w in ("size", "too large", "too many", "too big", "limit"))
    return False


def _request(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    """Send with retry on 5xx / 429 / timeouts / connection errors. Same body every try."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.request(method, url, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            err = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code < 500 and resp.status_code != 429:
                return resp
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if attempt == MAX_RETRIES - 1:
            raise RuntimeError(f"Keewano {method} {url} failed after {MAX_RETRIES} tries: {err}")
        delay = min(0.5 * 2 ** attempt, 8.0)
        _log(f"retry {attempt + 1}/{MAX_RETRIES - 1} in {delay:.1f}s ({err})")
        _sleep(delay)
    raise AssertionError("unreachable")


def register_events(client: httpx.Client | None = None) -> str:
    """Register schema.CUSTOM_EVENTS under schema.EVENTS_HASH. Safe to repeat."""
    global _registered
    if _registered:
        return _registered
    key = _env("KEEWANO_API_KEY")
    if client is None and not key:
        _log(f"dry-run: would register {len(schema.CUSTOM_EVENTS)} custom events (hash {schema.EVENTS_HASH})")
        return "dry_run"
    own = client is None
    client = client or _client(key)
    try:
        body = {"customEventsHash": schema.EVENTS_HASH, "eventTypes": schema.CUSTOM_EVENTS}
        resp = _request(client, "POST", f"{INGEST_URL}/custom", json=body)
        if resp.status_code == 409:
            raise RuntimeError(
                f"Keewano 409 conflict registering custom events (hash {schema.EVENTS_HASH}): {resp.text[:300]}. "
                "Either the hash collides with a different list, or an existing eventTypeId was re-registered with "
                "a new name/dataType. Registered events are append-only: add new events with new ids instead.")
        if resp.status_code in (401, 403):
            raise RuntimeError(f"Keewano rejected KEEWANO_API_KEY ({resp.status_code}): {resp.text[:200]}")
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"Keewano register failed: HTTP {resp.status_code} {resp.text[:300]}")
        try:
            status = str(resp.json().get("status") or "")
        except Exception:
            status = ""
        status = status or ("already_registered" if resp.status_code == 200 else "registered")
        if status == "in_progress":
            status = _wait_registered(client) or status
        _log(f"register: HTTP {resp.status_code} {status}")
        _registered = status
        return status
    finally:
        if own:
            client.close()


def _wait_registered(client: httpx.Client, timeout_s: float = 10.0) -> str | None:
    """Poll GET /custom while registration is in progress. Best effort."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        _sleep(1.0)
        try:
            resp = client.get(f"{INGEST_URL}/custom", params={"customEventsHash": schema.EVENTS_HASH})
            status = str(resp.json().get("status") or "") if resp.status_code == 200 else ""
        except Exception:
            return None
        if status and status != "in_progress":
            return status
    return None


def check_registration(client: httpx.Client | None = None) -> dict:
    """GET /custom for our hash (read-only). Returns {status_code, body}."""
    key = _env("KEEWANO_API_KEY")
    own = client is None
    client = client or _client(key)
    try:
        resp = client.get(f"{INGEST_URL}/custom", params={"customEventsHash": schema.EVENTS_HASH})
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:300]
        return {"status_code": resp.status_code, "body": body}
    finally:
        if own:
            client.close()


def build_payload(session_id: str, batch: int, install_id: str, events: list[schema.Event]) -> dict:
    return {
        "dataSessionId": session_id,
        "batch": batch,
        "customEventsHash": schema.EVENTS_HASH,
        "installId": install_id,
        "sdkVersion": SDK_VERSION,
        "events": [e.to_json() for e in events],
    }


def _ordered(events: list[schema.Event]) -> list[schema.Event]:
    """Keewano needs chronological events per entity. Stable sort keeps in-second order."""
    if all(events[i].ts <= events[i + 1].ts for i in range(len(events) - 1)):
        return events
    _log("warning: events not in timestamp order; stable-sorting by ts")
    return sorted(events, key=lambda e: e.ts)


def send_events(site_id: str, run_id: str, events: list[schema.Event], batch_size: int = 2000,
                dry_run: bool | None = None, client: httpx.Client | None = None) -> dict:
    """Send one run's events as one entity, one dataSessionId, batches 1..N in order."""
    t0 = time.monotonic()
    entity = schema.entity_id(site_id, run_id)
    key = _env("KEEWANO_API_KEY")
    if dry_run is None:
        dry_run = client is None and not key
    events = _ordered(list(events))
    session_id = str(uuid.uuid4())
    out = {"sent": 0, "accepted": 0, "batches": 0, "seconds": 0.0, "entity": entity,
           "session": session_id, "dry_run": dry_run}
    if not events:
        return out

    if dry_run:
        n = -(-len(events) // batch_size)
        first = build_payload(session_id, 1, entity, events[:2])
        _log(f"dry-run: {run_id} -> entity {entity}: {len(events)} events in {n} batches of <= {batch_size}")
        _log(f"dry-run: first batch head {json.dumps(first)[:300]}")
        out.update(sent=len(events), batches=n, seconds=round(time.monotonic() - t0, 3))
        return out

    own = client is None
    client = client or _client(key)
    url = f"{INGEST_URL}/in"
    i, batch, size, last_log = 0, 1, max(1, batch_size), t0
    try:
        while i < len(events):
            chunk = events[i:i + size]
            payload = build_payload(session_id, batch, entity, chunk)
            resp = _request(client, "POST", url, json=payload)
            if _too_large(resp):
                if size == 1:
                    raise RuntimeError(f"Keewano rejects even a 1-event batch as too large: {resp.text[:200]}")
                size = max(1, size // 2)
                _log(f"batch {batch} too large (HTTP {resp.status_code}); batch_size -> {size}")
                continue  # same batch number: the rejected batch was not ingested
            if resp.status_code in (401, 403):
                raise RuntimeError(f"Keewano rejected KEEWANO_API_KEY ({resp.status_code}): {resp.text[:200]}")
            if resp.status_code not in (200, 202):
                raise RuntimeError(f"Keewano batch {batch} failed: HTTP {resp.status_code} {resp.text[:300]}")
            try:
                body = resp.json()
            except Exception:
                body = {}
            accepted = int(body.get("accepted", len(chunk)) or 0)
            if accepted < len(chunk):
                _log(f"warning: batch {batch} accepted {accepted}/{len(chunk)} "
                     "(0 = duplicate or out-of-sequence; is this run_id already ingested?)")
            out["sent"] += len(chunk)
            out["accepted"] += accepted
            out["batches"] = batch
            i += len(chunk)
            batch += 1
            now = time.monotonic()
            if now - last_log > 2.0 or i >= len(events):
                _log(f"{run_id}: {i}/{len(events)} events, {out['batches']} batches, "
                     f"{out['accepted']} accepted, {now - t0:.1f}s")
                last_log = now
    finally:
        if own:
            client.close()
    out["seconds"] = round(time.monotonic() - t0, 3)
    return out


def send_run(run: schema.Run, **kw) -> dict:
    register_events(client=kw.get("client"))
    return send_events(run.site_id, run.run_id, run.events, **kw)


# --------------------------------------------------------------------------- SQL

SQL_VARS = ("KEEWANO_EMAIL", "KEEWANO_PAT", "KEEWANO_PROJECT_ID")


def sql_available() -> bool:
    """True when the SQL env vars are set (does not connect)."""
    return all(_env(v) for v in SQL_VARS)


def _plain(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def sql(query: str, params: Any = None) -> list[dict]:
    """Run one read-only query against Keewano's `events` table; rows as dicts."""
    missing = [v for v in SQL_VARS if not _env(v)]
    if missing:
        raise RuntimeError(f"Keewano SQL not configured: set {', '.join(missing)} in .env")
    if not query.lstrip().lower().startswith(("select", "(")):
        raise ValueError("Keewano SQL is read-only: query must start with SELECT")
    import psycopg
    from psycopg.rows import dict_row

    # autocommit: no BEGIN sent; ClientCursor: params bound client-side (no server prepare).
    with psycopg.connect(host=SQL_HOST, port=SQL_PORT, user=_env("KEEWANO_EMAIL"),
                         password=_env("KEEWANO_PAT"), dbname=_env("KEEWANO_PROJECT_ID"),
                         sslmode="require", connect_timeout=8, autocommit=True,
                         prepare_threshold=None, cursor_factory=psycopg.ClientCursor) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            if cur.description is None:
                return []
            return [{k: _plain(v) for k, v in row.items()} for row in cur.fetchall()]


# --------------------------------------------------------------------------- report

# metric -> (numerator column, denominator column, factor). Values are uint, so the SQL
# multiplies by a float literal first (the docs' `100.0 * count(..) / count(..)` pattern)
# to avoid integer division; Python recomputes from the raw sums either way.
REPORT_METRICS = {
    "pue": ("power_kwh", "it_kwh", 1.0),
    "wue": ("water_gal", "it_kwh", 3.785),
    "erf": ("heat_exported_kw", "power_kwh", 1.0),
}


def _check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid run_id {run_id!r}: expected like 'normal-7f3a9c' ({RUN_ID_RE.pattern})")
    return run_id


def report_queries(run_id: str) -> dict[str, str]:
    rid = _check_run_id(run_id)
    out = {}
    for name, (num, den, factor) in REPORT_METRICS.items():
        out[name] = (f"SELECT sum({num}) AS {num}, sum({den}) AS {den}, "
                     f"{factor!r} * sum({num}) / sum({den}) AS {name} "
                     f"FROM events WHERE run_id = '{rid}'")
    return out


def raw_report_queries(run_id: str) -> dict[str, str]:
    """Fallback without arithmetic, in case the ratio expression is rejected."""
    rid = _check_run_id(run_id)
    return {name: f"SELECT sum({num}) AS {num}, sum({den}) AS {den} FROM events WHERE run_id = '{rid}'"
            for name, (num, den, _) in REPORT_METRICS.items()}


def compute_metric(name: str, row: dict) -> float | None:
    num, den, factor = REPORT_METRICS[name]
    n, d = row.get(num), row.get(den)
    if n is None or not d:
        return None
    return factor * float(n) / float(d)


def run_report(run_id: str) -> dict:
    out = {}
    raw = raw_report_queries(run_id)
    for name, q in report_queries(run_id).items():
        try:
            rows = sql(q)
        except Exception as e:
            if "not configured" in str(e):
                raise
            _log(f"{name}: ratio query failed ({e}); falling back to raw sums")
            q = raw[name]
            rows = sql(q)
        row = rows[0] if rows else {}
        out[name] = {"value": compute_metric(name, row), "query": q, "raw": row}
    return out


# --------------------------------------------------------------------------- MCP

class KeewanoMCP:
    """async with KeewanoMCP() as k: await k.list_tools(); await k.call_tool(name, args)"""

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = url or _env("KEEWANO_MCP_URL") or DEFAULT_MCP_URL
        self.token = token or _env("KEEWANO_PAT")
        self._stack = None
        self.session = None

    async def __aenter__(self) -> "KeewanoMCP":
        if not self.token:
            raise RuntimeError("Keewano MCP not configured: set KEEWANO_PAT in .env")
        from contextlib import AsyncExitStack

        from mcp import ClientSession

        headers = {"k-token": self.token}
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            try:  # mcp >= 2: streamable_http_client(url, http_client=...)
                from mcp.client.streamable_http import streamable_http_client
                from mcp.shared._httpx_utils import create_mcp_http_client

                http = await self._stack.enter_async_context(create_mcp_http_client(headers=headers))
                streams = await self._stack.enter_async_context(streamable_http_client(self.url, http_client=http))
            except ImportError:  # mcp 1.x
                from mcp.client.streamable_http import streamablehttp_client

                streams = await self._stack.enter_async_context(streamablehttp_client(self.url, headers=headers))
            read, write = streams[0], streams[1]
            self.session = await self._stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
        except BaseException:
            await self._stack.__aexit__(*sys.exc_info())
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack:
            await self._stack.__aexit__(*exc)
        self._stack = self.session = None

    async def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            if cursor:
                from mcp import types

                res = await self.session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
            else:
                res = await self.session.list_tools()
            for t in res.tools:
                schema_ = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
                tools.append({"name": t.name, "description": t.description or "", "input_schema": schema_})
            cursor = getattr(res, "next_cursor", None) or getattr(res, "nextCursor", None)
            if not cursor:
                return tools

    async def call_tool(self, name: str, args: dict | None = None) -> str:
        res = await self.session.call_tool(name, args or {})
        parts = [c.text for c in (getattr(res, "content", None) or []) if getattr(c, "text", None)]
        if not parts:
            sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
            if sc is not None:
                parts.append(json.dumps(sc))
        text = "\n".join(parts)
        if getattr(res, "is_error", False) or getattr(res, "isError", False):
            return f"ERROR: {text}"
        return text


def mcp_list_tools() -> list[dict]:
    async def go():
        async with KeewanoMCP() as k:
            return await k.list_tools()
    return asyncio.run(go())


def mcp_call_tool(name: str, args: dict | None = None) -> str:
    async def go():
        async with KeewanoMCP() as k:
            return await k.call_tool(name, args)
    return asyncio.run(go())


# --------------------------------------------------------------------------- CLI

def _ping() -> None:
    now = int(time.time())
    events = [schema.Event("run_id", "ping-000000", now - 2), schema.Event("scenario", "normal", now - 1),
              schema.Event("hour", 0, now)]
    print("register:", register_events())
    print("send:", json.dumps(send_events("ping", "ping-000000", events)))


def _check() -> None:
    names = ["KEEWANO_API_KEY", "KEEWANO_PAT", "KEEWANO_MCP_URL", "KEEWANO_EMAIL", "KEEWANO_PROJECT_ID",
             "ANTHROPIC_API_KEY"]
    for n in names:
        print(f"  {n:20s} {'set' if _env(n) else 'MISSING'}")
    print(f"  events hash {schema.EVENTS_HASH}, {len(schema.CUSTOM_EVENTS)} custom events")
    if _env("KEEWANO_API_KEY"):
        try:
            print("ingest:", check_registration())
        except Exception as e:
            print("ingest: FAIL", e)
    else:
        print("ingest: skipped (no KEEWANO_API_KEY; sends are dry-run)")
    if _env("KEEWANO_PAT"):
        try:
            print(f"mcp: OK, {len(mcp_list_tools())} tools")
        except Exception as e:
            print("mcp: FAIL", repr(e))
    else:
        print("mcp: skipped (no KEEWANO_PAT)")
    if sql_available():
        try:
            print("sql: OK", sql("SELECT count(*) AS events, count(DISTINCT user_id) AS entities FROM events"))
        except Exception as e:
            print("sql: FAIL", e)
    else:
        print("sql: skipped (need " + ", ".join(v for v in SQL_VARS if not _env(v)) + ")")


# --- Alpine scaffold interface (send/query), kept so code written against it still works ---------

_FLOAT_TO_KW = {"heat_available_mw": "heat_available_kw", "heat_exported_mw": "heat_exported_kw",
                "heat_rejected_mw": "heat_rejected_kw", "heat_tower_mw": "reject_tower_kw"}


def send(events: list[dict], run_id: str, batch: int = 500) -> int:
    """Send scaffold-style float events ({"name", "value", "tags": {hour, day, month, scenario, run_id}}).
    They are converted to the integer schema (MW -> kW, storage_soc -> pct, signed heat_stored_mw split
    into stored/discharged, co2_t clamped at 0) and sent with send_events. Returns events accepted.
    thermal.run(push=True) no longer uses this; it sends its full Keewano rows via send_run."""
    by_hour: dict[int, dict] = {}
    for e in events:
        t = e.get("tags", {})
        row = by_hour.setdefault(int(t["hour"]), {"hour": int(t["hour"]), "day": int(t["day"]),
                                                  "month": int(t["month"]), "scenario": t.get("scenario")})
        row[e["name"]] = e["value"]
    out: list[schema.Event] = []
    first = True
    last_day = last_month = None
    for h in sorted(by_hour):
        r, ts = by_hour[h], schema.BASE_TS + h * 3600
        evs: list[tuple[str, Any]] = []
        if first:
            evs += [("run_id", run_id), ("scenario", r.get("scenario") or "normal")]
            first = False
        if r["day"] != last_day:
            evs.append(("day", r["day"])); last_day = r["day"]
        if r["month"] != last_month:
            evs.append(("month", r["month"])); last_month = r["month"]
        evs.append(("hour", h))
        if "decision" in r:
            evs.append(("decision", str(r["decision"])))
        for k, kk in _FLOAT_TO_KW.items():
            if k in r:
                evs.append((kk, float(r[k]) * 1000))
        if "heat_stored_mw" in r:
            v = float(r["heat_stored_mw"])
            evs += [("heat_stored_kw", max(v, 0) * 1000), ("heat_discharged_kw", max(-v, 0) * 1000)]
        for k in ("water_gal", "power_kwh", "it_kwh"):
            if k in r:
                evs.append((k, float(r[k])))
        if "cost_usd" in r:
            evs.append(("cost_usd", float(r["cost_usd"]) * 100))
        if "co2_t" in r:
            evs.append(("co2_kg", float(r["co2_t"]) * 1000))
        if "storage_soc" in r:
            evs.append(("storage_soc_pct", float(r["storage_soc"]) * 100))
        for i, (k, v) in enumerate(evs):
            dt = schema.EVENT_TYPE[k]
            val = str(v) if dt == "string" else int(round(float(v))) if dt == "price_usd_cent" else schema.to_int(v)
            out.append(schema.Event(k, val, ts + i))
    register_events()
    return send_events("lansing", run_id, out, batch_size=max(batch, 1))["accepted"]


def query(text: str) -> dict:
    """Plain-English question -> {"answer", "query", "rows"} (Claude + Keewano MCP, via agent.ask)."""
    import agent
    return agent.ask(text)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "check"
    if cmd == "check":
        _check()
    elif cmd == "ping":
        _ping()
    elif cmd == "tools":
        for t in mcp_list_tools():
            print(f"- {t['name']}: {t['description'].strip()}")
            print(f"    args: {json.dumps(t['input_schema'].get('properties', {}))[:300]}")
    elif cmd == "sql" and len(argv) > 1:
        for row in sql(argv[1]):
            print(json.dumps(row, default=str))
    elif cmd == "report" and len(argv) > 1:
        print(json.dumps(run_report(argv[1]), indent=2, default=str))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
