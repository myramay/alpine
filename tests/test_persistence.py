"""DATA_DIR persistence + abuse guards. Offline."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import guards
import paths
import sites

REPO = Path(__file__).resolve().parent.parent


def _fake_repo(tmp: Path) -> Path:
    r = tmp / "repo"
    (r / "outputs" / "runs").mkdir(parents=True)
    (r / "outputs" / "runs" / "index.json").write_text('[{"run_id": "normal-1"}]')
    (r / "outputs" / "runs" / "decisions_agent-1.json").write_text("{}")
    (r / "outputs" / "runs" / "normal-1.csv").write_text("x\n1\n")      # CSVs are not seeded (gitignored)
    (r / "outputs" / "report_cache.json").write_text('{"normal-1": {}}')
    (r / "sites" / "demo").mkdir(parents=True)
    (r / "sites" / "demo" / "meta.json").write_text('{"id": "demo", "status": "ready"}')
    (r / "sites" / "demo" / "site.yaml").write_text("site: {}\n")
    (r / "web" / "geo").mkdir(parents=True)
    (r / "web" / "geo" / "demo.json").write_text("{}")
    return r


def test_seed_copies_shipped_state_without_overwriting(tmp_path):
    repo, data = _fake_repo(tmp_path), tmp_path / "data"
    (data / "outputs" / "runs").mkdir(parents=True)
    (data / "outputs" / "runs" / "index.json").write_text('["mine"]')       # existing state wins
    n = paths.seed(data, repo)
    assert n == 5
    assert json.loads((data / "outputs" / "runs" / "index.json").read_text()) == ["mine"]
    assert (data / "outputs" / "runs" / "decisions_agent-1.json").exists()
    assert not (data / "outputs" / "runs" / "normal-1.csv").exists()
    assert (data / "outputs" / "report_cache.json").exists()
    assert (data / "sites" / "demo" / "meta.json").exists() and (data / "geo" / "demo.json").exists()
    assert (data / "out").is_dir()
    (data / "sites" / "demo" / "meta.json").write_text('{"id": "demo", "status": "edited"}')
    assert paths.seed(data, repo) == 0                                       # idempotent, never overwrites
    assert "edited" in (data / "sites" / "demo" / "meta.json").read_text()
    assert paths.seed(repo, repo) == 0                                       # unset DATA_DIR: no-op


def test_atomic_write(tmp_path, monkeypatch):
    p = tmp_path / "outputs" / "runs" / "index.json"
    paths.write_text_atomic(p, "[1]")
    assert p.read_text() == "[1]"

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        paths.write_text_atomic(p, "[2]")
    assert p.read_text() == "[1]"                                           # old content intact
    assert [f.name for f in p.parent.iterdir()] == ["index.json"]           # no temp files left


def test_thermal_index_write_is_atomic(tmp_path, monkeypatch):
    import thermal
    calls = []
    real = paths.write_text_atomic
    monkeypatch.setattr(paths, "write_text_atomic", lambda p, t: (calls.append(Path(p).name), real(p, t)))
    thermal.save_hourly([{"a": 1}], "normal-x", "normal", {"pue": 1.1}, {}, out_dir=str(tmp_path))
    assert calls == ["index.json"] and json.loads((tmp_path / "index.json").read_text())[0]["run_id"] == "normal-x"


@pytest.fixture
def datadir(tmp_path, monkeypatch):
    """sites.py pointed at a DATA_DIR (tmp) with the real repo as the shipped fallback."""
    data = tmp_path / "data"
    monkeypatch.setattr(sites, "SITES_DIR", data / "sites")
    monkeypatch.setattr(sites, "GEO_DIR", data / "geo")
    monkeypatch.setattr(sites, "REPO_SITES_DIR", REPO / "sites")
    return data


def test_created_site_lands_in_data_dir(datadir):
    sid = sites.reserve(name="Persist Town", it_mw=10)
    assert (datadir / "sites" / sid / "meta.json").exists()
    p = sites.paths(sid)
    assert p["dir"] == datadir / "sites" / sid and p["geo"] == datadir / "geo" / f"{sid}.json"
    ids = [m["id"] for m in sites.list_sites()]
    assert ids[0] == "lansing" and sid in ids and "ashburn_va" in ids        # merged with shipped sites
    assert sites.paths("ashburn_va")["dir"] == REPO / "sites" / "ashburn_va"  # shipped fallback
    assert sites.check_id("ashburn_va") == "ashburn_va"
    # DATA_DIR wins on id clash
    (datadir / "sites" / "ashburn_va").mkdir(parents=True)
    (datadir / "sites" / "ashburn_va" / "meta.json").write_text('{"id": "ashburn_va", "name": "mine"}')
    assert sites.read_meta("ashburn_va")["name"] == "mine"
    assert [m["id"] for m in sites.list_sites()].count("ashburn_va") == 1


def test_geo_served_from_data_dir_first(datadir, monkeypatch):
    from fastapi.testclient import TestClient
    import server
    (datadir / "geo").mkdir(parents=True)
    (datadir / "geo" / "only_here.json").write_text('{"where": "data"}')
    (datadir / "geo" / "lansing.json").write_text('{"where": "override"}')
    c = TestClient(server.app)
    assert c.get("/geo/only_here.json").json() == {"where": "data"}
    assert c.get("/geo/lansing.json").json() == {"where": "override"}
    assert c.get("/geo/ashburn_va.json").status_code == 200                  # repo fallback
    assert c.get("/geo/nope.json").status_code == 404
    assert c.get("/geo/..%2F..%2Fserver.py").status_code == 404


def test_rate_limit_429_and_reset(monkeypatch):
    t = [0.0]
    b = guards.TokenBucket(3, 3600, clock=lambda: t[0])
    assert [b.take("ip") for _ in range(3)] == [0, 0, 0]
    wait = b.take("ip")
    assert 0 < wait <= 1200
    assert b.take("other") == 0                                               # per IP
    t[0] += wait + 1
    assert b.take("ip") == 0                                                  # refilled


def test_rate_limit_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.delenv("RATE_LIMITS_OFF", raising=False)
    monkeypatch.setenv("RATE_LIMIT_SITES", "2/3600")
    guards.limiter.reset()
    monkeypatch.setattr(server, "MAX_BUILDS", 99)
    monkeypatch.setattr(sites, "start_site", lambda **kw: "fake_site")
    c = TestClient(server.app)
    hdr = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    for _ in range(3):   # validation errors don't use the quota
        assert c.post("/sites", json={"address": "x", "it_mw": 0}, headers=hdr).status_code == 422
    assert c.post("/sites", json={"address": "a"}, headers=hdr).status_code == 200
    assert c.post("/sites", json={"address": "b"}, headers=hdr).status_code == 200
    r = c.post("/sites", json={"address": "c"}, headers=hdr)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "rate limit" in r.json()["detail"]
    assert c.post("/sites", json={"address": "d"}, headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 200
    assert c.post("/run?push=false&scenario=nope&site=nope").status_code != 429  # push=false is not limited
    monkeypatch.setenv("RATE_LIMITS_OFF", "1")
    assert c.post("/sites", json={"address": "e"}, headers=hdr).status_code == 200
    guards.limiter.reset()


def test_body_cap():
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app)
    r = c.post("/ask", content=b'{"question": "' + b"x" * 40_000 + b'"}', headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_health_and_seed_with_data_dir_env(tmp_path):
    """A real process with DATA_DIR set: seeded from the repo, /health persistent, geo + sites served."""
    data = tmp_path / "vol"
    code = r"""
import json, sys
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)
h = c.get("/health").json()
s = [x["id"] for x in c.get("/sites").json()["sites"]]
print(json.dumps({"health": h, "sites": s, "geo": c.get("/geo/ashburn_va.json").status_code}))
"""
    env = {**os.environ, "DATA_DIR": str(data), "ALPINE_AUTORUN": "0"}
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["health"]["persistent"] is True and res["health"]["writable"] is True
    assert res["health"]["data_dir"] == str(data.resolve())
    assert "ashburn_va" in res["sites"] and res["geo"] == 200
    assert (data / "outputs" / "runs" / "index.json").exists() and (data / "sites" / "ashburn_va" / "site.yaml").exists()
    assert (data / "out").is_dir()
