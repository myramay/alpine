"""Agent tests: keewano, thermal and Anthropic are mocked; no network."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
from schema import Policy, Run  # noqa: E402


# ------------------------------------------------------------------ fakes

def make_hourly(days=10, high_water_day=2, unmet_day=4, empty_day=6):
    rows = []
    for d in range(days):
        for h in range(24):
            rows.append({
                "hour": d * 24 + h, "day": d,
                "water_gal": 50_000 if d == high_water_day else 1_000,
                "unmet_demand_kw": 500 if (d == unmet_day and h == 7) else 0,
                "storage_soc_pct": 5 if (d == empty_day and h < 8) else 50,
                "power_kwh": 100_000, "cost_usd": 1_100_000,
            })
    return rows


@pytest.fixture
def fake_keewano(monkeypatch):
    mod = types.ModuleType("keewano")
    mod.queries = []

    def sql(q):
        mod.queries.append(q)
        return [{"day": 0, "water_gal": 1}]
    mod.sql = sql
    mod.sql_available = lambda: True
    mod.send_run = lambda run: {"sent": len(run.hourly)}
    monkeypatch.setitem(sys.modules, "keewano", mod)
    return mod


@pytest.fixture
def fake_thermal(monkeypatch, tmp_path):
    mod = types.ModuleType("thermal")
    mod.calls = []

    def simulate(scenario="normal", run_id=None, policies=None, policy_notes=None, cfg=None, site_id=None):
        mod.calls.append({"scenario": scenario, "run_id": run_id, "policies": policies,
                          "policy_notes": policy_notes})
        hourly = make_hourly()
        for r in hourly:
            r["water_gal"] = r["water_gal"] // 2
        return Run(run_id=run_id, scenario=scenario, site_id="lansing", hourly=hourly)
    mod.simulate = simulate
    mod.save_run = lambda run: str(tmp_path / f"{run.run_id}.csv")
    monkeypatch.setitem(sys.modules, "thermal", mod)
    return mod


# ------------------------------------------------------------------ rules

def test_dry_boost_rises_after_high_water_day():
    p, reason, _ = agent.decide(Policy(), water=412_000, unmet_h=0, empty_h=0,
                                threshold=380_000, calm_streak=0)
    assert p.dry_boost == pytest.approx(0.1)
    assert "412,000 gal" in reason and "dry_boost" in reason


def test_dry_boost_capped_and_decays():
    p, _, _ = agent.decide(Policy(dry_boost=0.3), 500_000, 0, 0, 380_000, 0)
    assert p.dry_boost == pytest.approx(0.3)
    p, reason, _ = agent.decide(Policy(dry_boost=0.3), 100_000, 0, 0, 380_000, 0)
    assert p.dry_boost == pytest.approx(0.25)
    assert "decays" in reason


def test_precharge_after_unmet_demand():
    p, reason, calm = agent.decide(Policy(), 1_000, unmet_h=3, empty_h=0, threshold=380_000, calm_streak=5)
    assert p.precharge is True
    assert p.discharge_floor == pytest.approx(0.05)
    assert calm == 0 and "unmet" in reason


def test_precharge_after_long_empty_pit_only():
    p, _, _ = agent.decide(Policy(), 1_000, 0, empty_h=8, threshold=380_000, calm_streak=0)
    assert p.precharge is True and p.dry_boost == 0
    p, _, _ = agent.decide(Policy(), 1_000, 0, empty_h=6, threshold=380_000, calm_streak=0)
    assert p.precharge is False


def test_precharge_turns_off_after_calm_days():
    p, _, calm = agent.decide(Policy(precharge=True), 1_000, 0, 0, 380_000, calm_streak=2)
    assert calm == 3 and p.precharge is False


# ------------------------------------------------------------------ plan + log

def test_plan_year_offline_shape():
    daily = agent.fetch_daily_answers_offline("x", hourly=make_hourly())
    plan = agent.plan_year(daily, days=10)
    days = plan["days"]
    assert len(days) == 10
    assert days[3]["policy"]["dry_boost"] == pytest.approx(0.1)   # day 2 was high water
    assert days[5]["policy"]["precharge"] is True                   # day 4 had unmet demand
    for d in days[1:]:
        assert set(d) == {"day", "date", "questions", "policy", "reason", "changed"}
        assert len(d["questions"]) == 3
        for q in d["questions"]:
            assert {"question", "query", "answer"} <= set(q)
            assert q["source"] == "offline" and "offline" in q["query"]
    assert set(plan["notes"]) == {d["day"] for d in days if d["changed"]}
    assert all(len(n) <= 256 for n in plan["notes"].values())


def test_keewano_queries_grouped_by_day(fake_keewano):
    daily = agent.fetch_daily_answers_keewano("normal-7f3a")
    assert daily["source"] == "keewano"
    assert len(fake_keewano.queries) == 3
    for q in fake_keewano.queries:
        assert "run_id = 'normal-7f3a'" in q and "GROUP BY day" in q
    assert daily["answers"]["water_gal"]["by_day"] == {0: 1.0}


def test_run_daily_agent_writes_log_and_resimulates(monkeypatch, tmp_path, fake_keewano, fake_thermal):
    monkeypatch.setattr(agent, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(agent, "load_hourly_csv", lambda run_id: make_hourly())
    monkeypatch.setattr(agent, "fetch_daily_answers_keewano",
                        lambda run_id: agent.fetch_daily_answers_offline(run_id, make_hourly()))
    doc = agent.run_daily_agent("normal-1", push=True, agent_run_id="agent-test")
    path = tmp_path / "decisions_agent-test.json"
    saved = json.loads(path.read_text())
    assert saved["agent_run_id"] == "agent-test" and saved["baseline_run_id"] == "normal-1"
    assert len(saved["days"]) == 365
    call = fake_thermal.calls[0]
    assert call["scenario"] == "agent" and call["run_id"] == "agent-test"
    assert set(call["policy_notes"]) <= set(call["policies"])
    assert doc["push"] == {"sent": 240}
    assert doc["comparison"]["water_gal"]["agent"] < doc["comparison"]["water_gal"]["baseline"]


# ------------------------------------------------------------------ ask()

class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_ask_loop_runs_sql_then_answers(fake_keewano):
    rows = [{"pue": 1.18}]
    fake_keewano.sql = lambda q: rows
    q = "SELECT 1.0 * sum(power_kwh) / sum(it_kwh) AS pue FROM events WHERE run_id = 'normal-1'"
    tool_use = SimpleNamespace(type="tool_use", id="tu_1", name="run_sql", input={"query": q})
    first = SimpleNamespace(stop_reason="tool_use", content=[tool_use])
    final = SimpleNamespace(stop_reason="end_turn",
                            content=[SimpleNamespace(type="text", text="PUE was 1.18 for run normal-1.")])
    msgs = FakeMessages([first, final])
    client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))

    class FakeMCP:
        async def list_tools(self):
            return [{"name": "ask_keewano", "description": "ask", "input_schema": {"type": "object"}}]

        async def call_tool(self, name, args):
            raise AssertionError("not expected")

    out = asyncio.run(agent.ask_async("What was PUE?", client=client, mcp=FakeMCP()))
    assert out["answer"] == "PUE was 1.18 for run normal-1."
    assert out["query"] == q and out["rows"] == rows
    assert out["tool_calls"][0]["name"] == "run_sql" and out["tool_calls"][0]["ok"]
    first_call = msgs.calls[0]
    assert first_call["model"] == "claude-sonnet-5-5"
    assert {t["name"] for t in first_call["tools"]} == {"run_sql", "ask_keewano"}
    assert first_call["system"][0]["cache_control"] == {"type": "ephemeral"}
    # second request carries the tool_result back
    last_user = msgs.calls[1]["messages"][2]  # user, assistant(tool_use), user(tool_result)
    assert last_user["content"][0]["type"] == "tool_result"
    assert last_user["content"][0]["tool_use_id"] == "tu_1"
