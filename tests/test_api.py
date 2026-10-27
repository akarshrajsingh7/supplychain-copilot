import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.api.main import app
from backend.engine import IncidentEngine
from backend.graph.graphiti_client import TemporalMemory


@pytest.mark.asyncio
async def test_api_and_durable_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    memory = TemporalMemory(path=tmp_path / "memory.sqlite")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        engine = IncidentEngine(memory, saver, state_path=tmp_path / "incident.json")
        await engine.initialize()
        app.state.engine = engine
        app.state.task = None
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/health")).json()["ok"]
            assert (await client.post("/api/decision", json={"decision": "APPROVE"})).status_code == 409
            assert (await client.post("/api/incidents")).status_code == 200
            assert (await client.post("/api/incidents")).status_code == 409
            await app.state.task
            assert engine.state["status"] == "AWAITING HUMAN APPROVAL"
            evidence = await client.get("/api/evaluation?download=true")
            assert evidence.status_code == 200
            assert "attachment" in evidence.headers["content-disposition"]
            report = evidence.json()
            assert report["metrics"]["read_tool_attempts"] > 0
            assert report["checkpoint"]["pending_interrupts"] == 1
            assert report["summary"]["fail"] == 0
            # Reconstruct the engine against the same SQLite checkpoint, as after a server restart.
            restored = IncidentEngine(memory, saver, state_path=tmp_path / "incident.json")
            app.state.engine = restored
            response = await client.post("/api/decision", json={"decision": "REJECT"})
            assert response.json()["status"] == "REJECTED"
            assert not any(f["kind"] == "DECISION" for f in await memory.search_graphiti_memory())
            assert (await client.post("/api/decision", json={"decision": "APPROVE"})).status_code == 409
            assert (await client.get("/api/graph?view=before")).json()["nodes"][0]["risk"] == "healthy"
            assert (await client.post("/api/reset")).json()["status"] == "HEALTHY"
