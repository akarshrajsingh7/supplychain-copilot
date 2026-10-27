import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.engine import IncidentEngine
from backend.evaluation import evaluate_run
from backend.graph.graphiti_client import TemporalMemory
from backend.models.runs import RunPolicy


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    instance = IncidentEngine(TemporalMemory(path=tmp_path/"memory.sqlite"), InMemorySaver(),
                             state_path=tmp_path/"state.json")
    await instance.initialize()
    return instance


@pytest.mark.asyncio
async def test_injected_failure_retries_a_read_and_preserves_approval(engine):
    await engine.inject(RunPolicy(simulate_tool_failure=True))
    await engine.run()
    assert engine.state["status"] == "AWAITING HUMAN APPROVAL", engine.state["error"]
    failures = [e for e in engine.state["events"] if e["stage"] == "TOOL_ERROR"]
    recoveries = [e for e in engine.state["events"] if e["stage"] == "RECOVERED"]
    assert len(failures) == len(recoveries) == 1
    assert failures[0]["data"]["call_id"] == recoveries[0]["data"]["call_id"]
    assert failures[0]["data"]["attempt"] == 1
    assert recoveries[0]["data"]["attempt"] == 2
    assert not any(f["kind"] == "DECISION" for f in engine.state["evidence"])
    report = await evaluate_run(engine)
    assert report["summary"] == {"pass": 10, "pending": 0, "fail": 0}
    assert report["metrics"]["retries"] == 1
    assert report["checkpoint"]["pending_interrupts"] == 1
    await engine.decide("APPROVE")
    approved = await evaluate_run(engine)
    assert approved["checkpoint"]["approval_interrupt_observed"]
    assert approved["summary"]["fail"] == 0
    assert approved["spending_check"]["incremental_cost_eur"] == 4990
    assert engine.state["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_spending_limit_blocks_decision_and_interrupt(engine):
    await engine.inject(RunPolicy(max_incremental_cost_eur=3000))
    await engine.run()
    assert engine.state["status"] == "POLICY BLOCKED", engine.state["error"]
    assert engine.state["policy_check"] == {
        "allowed": False, "incremental_cost_eur": 4990, "limit_eur": 3000, "headroom_eur": -1990}
    assert not any(f["kind"] == "DECISION" for f in await engine.memory.search_graphiti_memory())
    assert not (await engine.agent.aget_state(engine.config())).interrupts
    with pytest.raises(ValueError, match="No pending"):
        await engine.decide("APPROVE")
    report = await evaluate_run(engine)
    assert next(c for c in report["checks"] if c["id"] == "spending")["status"] == "pass"


@pytest.mark.asyncio
async def test_read_attempt_cap_stops_execution(engine):
    await engine.inject(RunPolicy(max_tool_attempts=2))
    await engine.run()
    assert engine.state["status"] == "ERROR"
    assert "budget exhausted" in engine.state["error"]
    assert engine.state["tool_attempts"] == 2
    assert len([e for e in engine.state["events"] if e["stage"] == "TOOL_START"]) == 2
    assert any(e["stage"] == "LIMIT" for e in engine.state["events"])
    assert not engine.state["verified"]


@pytest.mark.asyncio
async def test_invalid_operation_is_not_retried(engine):
    calls = []

    def invalid():
        calls.append(1)
        raise ValueError("Invalid input")

    with pytest.raises(ValueError):
        await engine.read_tool("Test", "controlled_read", invalid)
    assert calls == [1]
    assert not any(e["stage"] == "RETRY" for e in engine.state["events"])


@pytest.mark.asyncio
async def test_report_requires_tool_records_not_completed_role_claims(engine):
    engine.state["status"] = "AWAITING HUMAN APPROVAL"
    engine.state["completed_roles"] = ["policy-analyst", "impact-analyst", "inventory-analyst",
                                       "sourcing-analyst", "mitigation-planner", "risk-critic"]
    report = await evaluate_run(engine)
    assert next(c for c in report["checks"] if c["id"] == "specialists")["status"] == "fail"
    assert next(c for c in report["checks"] if c["id"] == "checkpoint")["status"] == "fail"
