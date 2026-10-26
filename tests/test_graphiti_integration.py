"""Run with RUN_NEO4J_TESTS=1 against a disposable demo database."""
import os
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from backend.engine import IncidentEngine
from backend.graph.graphiti_client import TemporalMemory, GROUP, uid


@pytest.mark.skipif(os.getenv("RUN_NEO4J_TESTS") != "1", reason="Opt-in Neo4j integration")
@pytest.mark.asyncio
async def test_graphiti_world_model(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    memory = TemporalMemory("graphiti", path=tmp_path / "memory.sqlite")
    await memory.connect()
    try:
        engine = IncidentEngine(memory, InMemorySaver(), state_path=tmp_path / "state.json")
        await engine.initialize()
        graph = await memory.load_supply_graph()
        assert len(graph.nodes) == 68
        assert len(graph.edges) == 123
        await engine.inject()
        mentions, _, _ = await memory.client.driver.execute_query(
            "MATCH (e:Episodic {uuid:$id})-[:MENTIONS]->(n:Entity) RETURN count(n) AS count",
            id=uid(engine.state["run_id"]+":tariff"))
        assert mentions[0]["count"] == 4
        await engine.run()
        assert engine.state["status"] == "AWAITING HUMAN APPROVAL", engine.state["error"]
        await engine.decide("APPROVE")
        facts = await memory.search_graphiti_memory(subject="A")
        assert any(f["predicate"] == "MITIGATION_DECISION" for f in facts)
        before = await memory.search_graphiti_memory(subject="A", as_of="2026-10-25T12:00:00+00:00")
        assert any("8.50" in f["object"] for f in before)
    finally:
        await memory.close()
