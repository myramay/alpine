"""Keewano event sender. Owner: Myra.

Interface:
    send(events: list[dict], run_id: str, batch: int = 500) -> int   # events sent
    query(text: str) -> dict                                          # plain-English via MCP, {answer, query, rows}

An event dict: {"name": str, "value": float|str, "tags": {"hour", "day", "month", "scenario", "run_id"}}.
Entity id for every event: f"lansing:{run_id}".

Fill in the exact REST and MCP shapes from the SpeedHacks quickstart:
https://docs.keewano.com/getting-started/speedhacks-quickstart
"""
from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ.get("KEEWANO_API_KEY", "")
INGEST_URL = os.environ.get("KEEWANO_INGEST_URL", "")
MCP_URL = os.environ.get("KEEWANO_MCP_URL", "")


def send(events: list[dict], run_id: str, batch: int = 500) -> int:
    if not (API_KEY and INGEST_URL):
        raise RuntimeError("KEEWANO_API_KEY / KEEWANO_INGEST_URL not set")
    sent = 0
    with httpx.Client(timeout=30) as c:
        for i in range(0, len(events), batch):
            chunk = events[i:i + batch]
            # TODO(Myra): match the quickstart's payload shape (entity, name, value, tags).
            r = c.post(INGEST_URL, headers={"Authorization": f"Bearer {API_KEY}"},
                       json={"entity": f"lansing:{run_id}", "events": chunk})
            r.raise_for_status()
            sent += len(chunk)
    return sent


def query(text: str) -> dict:
    # TODO(Myra): open the MCP client (see `mcp` package), call the Keewano query tool with the
    # SQL-style query that agent.py produces, return {"answer", "query", "rows"}.
    raise NotImplementedError
