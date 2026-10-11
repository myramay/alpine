"""Offline tests for keewano.py (no network)."""
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import keewano  # noqa: E402
import schema  # noqa: E402


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for v in ("KEEWANO_API_KEY", "KEEWANO_PAT", "KEEWANO_EMAIL", "KEEWANO_PROJECT_ID"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(keewano, "_sleep", lambda s: None)
    monkeypatch.setattr(keewano, "_registered", None)


def make_events(n):
    out = [schema.Event("run_id", "normal-abc123", schema.BASE_TS)]
    for i in range(1, n):
        out.append(schema.Event("hour", i, schema.BASE_TS + i))
    return out


class Recorder:
    """MockTransport handler: records ingest payloads; `script` maps call index -> response."""

    def __init__(self, script=None, max_events=None):
        self.calls, self.script, self.max_events = [], script or {}, max_events

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        idx = len(self.calls)
        self.calls.append(body)
        if request.url.path.endswith("/custom"):
            return httpx.Response(200, json={"status": "already_registered"})
        if idx in self.script:
            return self.script[idx]
        if self.max_events and len(body["events"]) > self.max_events:
            return httpx.Response(413, text="payload too large")
        return httpx.Response(202, json={"received": len(body["events"]), "accepted": len(body["events"])})


def client_for(rec):
    return httpx.Client(transport=httpx.MockTransport(rec), headers={"k-token": "test"})


def test_batching_order_and_numbers():
    events = make_events(25)
    rec = Recorder()
    r = keewano.send_events("site", "normal-abc123", events, batch_size=10, client=client_for(rec))
    assert [len(c["events"]) for c in rec.calls] == [10, 10, 5]
    assert [c["batch"] for c in rec.calls] == [1, 2, 3]
    assert len({c["dataSessionId"] for c in rec.calls}) == 1
    flat = [e for c in rec.calls for e in c["events"]]
    assert flat == [e.to_json() for e in events]
    assert r["sent"] == 25 and r["accepted"] == 25 and r["batches"] == 3
    assert r["entity"] == schema.entity_id("site", "normal-abc123")


def test_new_session_per_call():
    rec = Recorder()
    c = client_for(rec)
    keewano.send_events("s", "normal-abc123", make_events(3), client=c)
    keewano.send_events("s", "normal-abc123", make_events(3), client=c)
    assert rec.calls[0]["dataSessionId"] != rec.calls[1]["dataSessionId"]
    assert rec.calls[1]["batch"] == 1


def test_payload_shape():
    events = make_events(3)
    p = keewano.build_payload(str(uuid.uuid4()), 1, schema.entity_id("s", "r"), events)
    assert set(p) == {"dataSessionId", "batch", "customEventsHash", "installId", "sdkVersion", "events"}
    assert p["customEventsHash"] == schema.EVENTS_HASH
    assert p["sdkVersion"] == "thermal-layer/0.1"
    uuid.UUID(p["installId"])
    uuid.UUID(p["dataSessionId"])
    e = p["events"][1]
    assert set(e) == {"eventTypeId", "dataType", "timestamp", "data"}
    assert e == {"eventTypeId": 2002, "dataType": "uint", "timestamp": schema.BASE_TS + 1, "data": 1}


def test_dry_run_without_key(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used in dry run")
    monkeypatch.setattr(httpx.Client, "request", boom)
    r = keewano.send_events("s", "normal-abc123", make_events(4500))
    assert r["dry_run"] is True and r["sent"] == 4500 and r["batches"] == 3 and r["accepted"] == 0
    assert keewano.register_events() == "dry_run"
    run = schema.Run(run_id="normal-abc123", scenario="normal", site_id="s", events=make_events(5))
    assert keewano.send_run(run)["sent"] == 5


def test_retry_on_503_reuses_batch_number():
    rec = Recorder(script={1: httpx.Response(503, text="busy"), 2: httpx.Response(503, text="busy")})
    keewano.send_events("s", "normal-abc123", make_events(20), batch_size=10, client=client_for(rec))
    assert [c["batch"] for c in rec.calls] == [1, 2, 2, 2]
    assert rec.calls[1] == rec.calls[2] == rec.calls[3]


def test_retry_on_timeout():
    n = {"i": 0}

    def handler(request):
        n["i"] += 1
        if n["i"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(202, json={"received": 2, "accepted": 2})
    r = keewano.send_events("s", "normal-abc123", make_events(2), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert r["accepted"] == 2 and n["i"] == 2


def test_halving_on_413():
    events = make_events(20)
    rec = Recorder(max_events=5)
    r = keewano.send_events("s", "normal-abc123", events, batch_size=20, client=client_for(rec))
    ok = [c for c in rec.calls if len(c["events"]) <= 5]
    assert [len(c["events"]) for c in rec.calls] == [20, 10, 5, 5, 5, 5]
    assert [c["batch"] for c in ok] == [1, 2, 3, 4]
    assert [e for c in ok for e in c["events"]] == [e.to_json() for e in events]
    assert r["sent"] == 20 and r["batches"] == 4


def test_halving_on_400_size_message():
    rec = Recorder(script={0: httpx.Response(400, json={"error": "batch exceeds max size"})})
    r = keewano.send_events("s", "normal-abc123", make_events(8), batch_size=8, client=client_for(rec))
    assert [len(c["events"]) for c in rec.calls] == [8, 4, 4]
    assert r["batches"] == 2


def test_other_400_raises():
    rec = Recorder(script={0: httpx.Response(400, json={"error": "bad eventTypeId"})})
    with pytest.raises(RuntimeError, match="batch 1"):
        keewano.send_events("s", "normal-abc123", make_events(3), client=client_for(rec))


def test_register_conflict_and_ok():
    rec = Recorder()
    assert keewano.register_events(client=client_for(rec)) == "already_registered"
    assert rec.calls[0] == {"customEventsHash": schema.EVENTS_HASH, "eventTypes": schema.CUSTOM_EVENTS}
    keewano._registered = None
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(409, text="conflict")))
    with pytest.raises(RuntimeError, match="409"):
        keewano.register_events(client=c)


@pytest.mark.parametrize("bad", ["x' OR '1'='1", "normal-abc123'; --", "Normal-ABC123", "", "normal abc123",
                                 "normal-abc123' UNION SELECT 1 --"])
def test_report_queries_rejects_bad_run_id(bad):
    with pytest.raises(ValueError):
        keewano.report_queries(bad)


def test_report_queries_shape():
    q = keewano.report_queries("heat_wave-7f3a9c")
    assert set(q) == {"pue", "wue", "erf"}
    for s in q.values():
        assert s.startswith("SELECT ") and s.endswith("FROM events WHERE run_id = 'heat_wave-7f3a9c'")
    assert "3.785 * sum(water_gal) / sum(it_kwh)" in q["wue"]
    assert "1.0 * sum(power_kwh) / sum(it_kwh)" in q["pue"]
    assert "1.0 * sum(heat_exported_kw) / sum(power_kwh)" in q["erf"]
    assert keewano.compute_metric("pue", {"power_kwh": 130, "it_kwh": 100}) == pytest.approx(1.3)
    assert keewano.compute_metric("wue", {"water_gal": 100, "it_kwh": 1000}) == pytest.approx(0.3785)
    assert keewano.compute_metric("erf", {"heat_exported_kw": 0, "power_kwh": 0}) is None


def test_sql_not_configured():
    assert keewano.sql_available() is False
    with pytest.raises(RuntimeError, match="KEEWANO_EMAIL.*KEEWANO_PAT.*KEEWANO_PROJECT_ID"):
        keewano.sql("SELECT count(*) FROM events")
