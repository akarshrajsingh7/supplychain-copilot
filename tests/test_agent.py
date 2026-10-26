import pytest
from langgraph.checkpoint.memory import InMemorySaver
from backend.engine import IncidentEngine
from backend.graph.graphiti_client import TemporalMemory


@pytest.mark.asyncio
async def test_real_deep_agent_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    memory = TemporalMemory(path=tmp_path / "memory.sqlite")
    engine = IncidentEngine(memory, InMemorySaver(), state_path=tmp_path / "state.json")
    await engine.initialize()
    await engine.inject()
    await engine.run()
    assert engine.state["status"] == "AWAITING HUMAN APPROVAL", engine.state["error"]
    assert engine.state["rejected"]
    assert engine.state["verified"]
    assert not any(f["kind"] == "DECISION" for f in engine.state["evidence"])
    await engine.decide("APPROVE")
    assert engine.state["status"] == "APPROVED"
    assert any(f["kind"] == "DECISION" for f in await memory.search_graphiti_memory())
    assert any(n["risk"] == "mitigated" for n in engine.graph_view()["nodes"] if n["type"] == "Order")
    assert all(n["risk"] == "healthy" for n in engine.graph_view(before=True)["nodes"])
    # A second rehearsal must not backdate its critic evidence before the last success.
    previous_time = await memory.latest_fact_time()
    engine.state = engine.fresh_state()
    await engine.inject()
    await engine.run()
    assert engine.state["status"] == "AWAITING HUMAN APPROVAL", engine.state["error"]
    assert await memory.latest_fact_time() > previous_time
    await engine.decide("REJECT")
    assert engine.state["status"] == "REJECTED"
