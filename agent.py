"""The agent. Owner: Myra.

Two jobs:
  1. ask(question) -> {"answer", "query", "rows"}: Claude gets the event schema (docs/EVENTS.md),
     the ten example questions (questions.md), and the Keewano query tool; writes a query, runs it,
     answers in one sentence with the number.
  2. daily_loop(run_id): for each simulated day, ask Keewano three fixed questions about yesterday
     and set today's policy. Append {"day", "question", "answer", "decision", "changed"} to
     out/decisions.json and emit a `policy_change` event when something changed.

  report() -> the three metrics, each from its own Keewano query (see docs/EVENTS.md).

Model: claude-sonnet-5-5 for ask(); switch daily_loop to claude-haiku-5-5 if it is slow.
"""
from __future__ import annotations

from pathlib import Path

SCHEMA = Path("docs/EVENTS.md").read_text()
EXAMPLES = Path("questions.md").read_text()

DAILY_QUESTIONS = [
    "How many gallons of water did the site use yesterday?",
    "How many hours yesterday was heat demand higher than heat available?",
    "How many hours yesterday was the pit at or below its minimum level?",
]

# Thresholds the rules use on the three answers. Tune after the first run.
WATER_GAL_PER_DAY_HIGH = 200_000
UNMET_HOURS_HIGH = 4
PIT_EMPTY_HOURS_HIGH = 6


def decide(water_gal: float, unmet_hours: int, pit_empty_hours: int, policy: dict) -> tuple[dict, str]:
    """Pure rules on yesterday's answers. Returns (new_policy, reason)."""
    p, why = dict(policy), []
    if water_gal > WATER_GAL_PER_DAY_HIGH:
        p["prefer_dry"] = True
        p["tower_cap_mw"] = min(p.get("tower_cap_mw", 1e9), 20.0)
        why.append(f"water {water_gal:,.0f} gal > {WATER_GAL_PER_DAY_HIGH:,}: cap towers at 20 MW, sell and store first")
    if unmet_hours > UNMET_HOURS_HIGH:
        p["storage_reserve"] = min(0.6, p.get("storage_reserve", 0.2) + 0.1)
        why.append(f"{unmet_hours} h unmet demand: raise storage reserve to {p['storage_reserve']:.1f}")
    if pit_empty_hours > PIT_EMPTY_HOURS_HIGH:
        p["storage_reserve"] = min(0.6, p.get("storage_reserve", 0.2) + 0.1)
        why.append(f"pit empty {pit_empty_hours} h: charge earlier")
    return p, "; ".join(why) or "no change"


def ask(question: str) -> dict:
    # TODO(Myra): anthropic Messages API with a `keewano_query` tool; loop until final text.
    raise NotImplementedError


def report() -> dict:
    # TODO(Myra): three keewano.query() calls, return {"run_id","cached":False,"computed_at","metrics":[...]}
    raise NotImplementedError


def daily_loop(run_id: str) -> None:
    # TODO(Myra): for day in 0..364: ask DAILY_QUESTIONS with "where day = d-1", decide(), log, emit.
    raise NotImplementedError


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    daily_loop(ap.parse_args().run)
