"""Where Alpine reads and writes files.

DATA_DIR (env, absolute path, e.g. a Railway Volume mounted at /data) holds every file the app writes:
  DATA_DIR/sites/<id>/       user-created sites (site.yaml, csvs, meta.json, raw/)
  DATA_DIR/geo/<id>.json     their 3D-map files (served by /geo/{name} before the repo's web/geo)
  DATA_DIR/outputs/runs/     Keewano runs: <run_id>.csv, index.json, decisions_<id>.json
  DATA_DIR/outputs/report_cache.json
  DATA_DIR/out/              the page's local runs (run_*.json, events_*.csv, decisions.json)
Built-in read-only data (config/, data/, web/geo/lansing.json, the shipped sites/) stays in the repo.

Without DATA_DIR everything is exactly where it always was: the repo root (out/ and the thermal.py
outputs/runs relative to the working directory, as before), so local dev and the tests are unchanged.
seed() copies the repo's shipped state into an empty or partial DATA_DIR, never overwriting.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
_env = os.environ.get("DATA_DIR", "").strip()
PERSISTENT = bool(_env)
DATA_DIR = Path(_env).expanduser().resolve() if PERSISTENT else REPO_ROOT

REPO_SITES_DIR = REPO_ROOT / "sites"          # shipped example sites (read-only on a deploy)
REPO_GEO_DIR = REPO_ROOT / "web" / "geo"      # lansing.json + shipped sites' maps
REPO_RUNS_DIR = REPO_ROOT / "outputs" / "runs"

SITES_DIR = DATA_DIR / "sites"
GEO_DIR = DATA_DIR / "geo" if PERSISTENT else REPO_GEO_DIR
OUTPUTS_DIR = DATA_DIR / "outputs"
RUNS_DIR = OUTPUTS_DIR / "runs"
REPORT_CACHE_PATH = OUTPUTS_DIR / "report_cache.json"
# Historically cwd-relative ("out", "outputs/runs" in thermal.py); kept that way when DATA_DIR is unset.
OUT_DIR = DATA_DIR / "out" if PERSISTENT else Path("out")
LOCAL_RUNS_DIR = RUNS_DIR if PERSISTENT else Path("outputs") / "runs"


def write_text_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory + os.replace, so readers never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _copy_missing(src: Path, dst: Path) -> int:
    if not src.exists() or dst.exists():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return 1


def seed(data_dir: Path | None = None, repo: Path | None = None) -> int:
    """Copy the repo's shipped state into DATA_DIR where missing (never overwrites). Returns files copied.
    No-op when DATA_DIR is the repo itself."""
    data_dir = Path(data_dir or DATA_DIR)
    repo = Path(repo or REPO_ROOT)
    if data_dir.resolve() == repo.resolve():
        return 0
    n = 0
    runs_src, runs_dst = repo / "outputs" / "runs", data_dir / "outputs" / "runs"
    n += _copy_missing(runs_src / "index.json", runs_dst / "index.json")
    for f in sorted(runs_src.glob("decisions_*.json")):
        n += _copy_missing(f, runs_dst / f.name)
    n += _copy_missing(repo / "outputs" / "report_cache.json", data_dir / "outputs" / "report_cache.json")
    for d in sorted((repo / "sites").glob("*")):
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file():
                n += _copy_missing(f, data_dir / "sites" / d.name / f.relative_to(d))
        n += _copy_missing(repo / "web" / "geo" / f"{d.name}.json", data_dir / "geo" / f"{d.name}.json")
    for sub in ("sites", "geo", "outputs/runs", "out"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    return n


def writable(path: Path | None = None) -> bool:
    path = Path(path or DATA_DIR)
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path, prefix=".probe.")
        os.close(fd)
        os.unlink(tmp)
        return True
    except OSError:
        return False
