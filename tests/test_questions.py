import json
import os

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langsmith.utils import tracing_is_enabled

from backend.api.main import app
from backend.engine import IncidentEngine
from backend.graph.graphiti_client import TemporalMemory
from backend.models.questions import GraphQuestion
from backend.qa import QuestionError, answer_graph_question, build_question_context, create_question_model, question_provider_catalog, compact_local_context, PROVIDERS


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    memory = TemporalMemory(path=tmp_path / "questions.sqlite")
    instance = IncidentEngine(memory, InMemorySaver(), state_path=tmp_path / "incident.json")
    await instance.initialize()
    instance.save()
    return instance


def question(**updates):
    return GraphQuestion(question="Why is Supplier B constrained?", provider="ollama", model="qwen3:8b", **updates)


@pytest.mark.asyncio
async def test_current_and_before_context_are_distinct_and_read_only(engine):
    await engine.inject()
    await engine.run()
    before_state = json.dumps(engine.snapshot(), sort_keys=True)
    current, sources = await build_question_context(engine, question(selected_entity="B"))
    index = {s.id: s for s in sources}
    assert index["C:impact"].data["tariff"] == 12300
    assert index["C:plan"].data["assessment"]["savings"] == 7310
    assert index["C:rejection"].data["accepted"] is False
    assert current["selected_entity"] == "B"
    historical, old_sources = await build_question_context(engine, question(view="before"))
    old = {s.id: s for s in old_sources}
    assert historical["incident_status"] == "BEFORE DISRUPTION"
    assert "C:impact" not in old and "C:plan" not in old and "C:rejection" not in old
    assert not any(s.data.get("type") in {"Policy", "Mitigation"} for s in old_sources)
    offer_a = next(o for o in old["C:offers"].data["offers"] if o["supplier"] == "A")
    assert offer_a["landed_cost_per_unit_eur"] == 8.5
    assert json.dumps(engine.snapshot(), sort_keys=True) == before_state


@pytest.mark.asyncio
async def test_grounded_response_uses_request_key_and_validates_sources(engine, monkeypatch):
    calls = []
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    class Model:
        async def ainvoke(self, messages):
            assert not tracing_is_enabled()
            prompt = messages[-1].content
            assert "test-user-secret" not in prompt
            payload = json.loads(prompt)
            assert any(s["id"] == "N:B" for s in payload["snapshot"]["sources"])
            return AIMessage(content=json.dumps({"answer": "Formosa Silicon has 2,000 units of spare capacity.",
                                                 "citations": ["N:B", "N:B", "invented-source"]}))

    def factory(provider, model, key):
        calls.append((provider, model, key))
        return Model()

    monkeypatch.setattr("backend.qa.create_question_model", factory)
    initial_key = os.getenv("OPENAI_API_KEY")
    result = await answer_graph_question(engine, GraphQuestion(question="What is B's spare capacity?",
        provider="openai", model="gpt-4.1-mini"), "test-user-secret")
    assert calls == [("openai", "gpt-4.1-mini", "test-user-secret")]
    assert [s.id for s in result.sources] == ["N:B"]
    assert result.warnings
    assert "test-user-secret" not in result.model_dump_json()
    assert os.getenv("OPENAI_API_KEY") == initial_key
    assert "test-user-secret" not in engine.state_path.read_text()
    assert not any("test-user-secret" in str(f) for f in await engine.memory.search_graphiti_memory())


@pytest.mark.asyncio
async def test_provider_error_does_not_leak_key(engine, monkeypatch, caplog):
    class AuthenticationFailure(Exception):
        status_code = 401

    class Model:
        async def ainvoke(self, messages):
            raise AuthenticationFailure("Bad token: test-user-secret")

    monkeypatch.setattr("backend.qa.create_question_model", lambda *args: Model())
    with pytest.raises(QuestionError) as error:
        await answer_graph_question(engine, GraphQuestion(question="What is B's capacity?", provider="openai", model="gpt-4.1-mini"), "test-user-secret")
    assert error.value.status == 401
    assert "test-user-secret" not in caplog.text
    assert "test-user-secret" not in error.value.message


@pytest.mark.asyncio
async def test_question_api_validation_and_missing_keys(engine, monkeypatch):
    app.state.engine = engine
    monkeypatch.setenv("OPENAI_API_KEY", "server-key-must-not-be-used-implicitly")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        catalog = (await client.get("/api/qa/models")).json()
        assert {p["id"] for p in catalog["providers"]} == {"ollama", "openai", "anthropic", "google"}
        body = {"question": "Show supplier capacity", "provider": "openai", "model": "gpt-4.1-mini"}
        assert (await client.post("/api/qa/ask", json=body)).status_code == 400
        invalid = await client.post("/api/qa/ask", json={**body, "provider": "unknown"}, headers={"X-LLM-API-Key": "secret-header"})
        assert invalid.status_code == 422
        assert "secret-header" not in invalid.text
        assert (await client.post("/api/qa/ask", json={**body, "base_url": "http://untrusted"})).status_code == 422
        with pytest.raises(QuestionError, match="selected entity"):
            await build_question_context(engine, question(selected_entity="not-an-entity"))


@pytest.mark.asyncio
async def test_local_model_failure_is_clear_and_format_error_is_safe(engine, monkeypatch):
    class Model:
        async def ainvoke(self, messages):
            raise ConnectionError("local host unavailable")

    monkeypatch.setattr("backend.qa.create_question_model", lambda *args: Model())
    with pytest.raises(QuestionError) as error:
        await answer_graph_question(engine, question())
    assert error.value.status == 503
    assert "Ollama" in error.value.message

    class InvalidModel:
        async def ainvoke(self, messages):
            return AIMessage(content="Not a JSON answer")

    monkeypatch.setattr("backend.qa.create_question_model", lambda *args: InvalidModel())
    with pytest.raises(QuestionError) as error:
        await answer_graph_question(engine, question())
    assert error.value.status == 502


@pytest.mark.parametrize("provider,model,expected", [
    ("ollama", "qwen3:8b", "ChatOllama"),
    ("openai", "gpt-4.1-mini", "ChatOpenAI"),
    ("anthropic", "claude-sonnet-5-5", "ChatAnthropic"),
    ("google", "gemini-3.8-flash", "ChatGoogleGenerativeAI"),
])
def test_adapter_construction_matches_installed_apis(provider, model, expected):
    assert type(create_question_model(provider, model, "test-not-a-real-key")).__name__ == expected


@pytest.mark.asyncio
async def test_installed_local_models_appear_first_without_mutating_presets(monkeypatch):
    original = json.dumps(PROVIDERS)

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            from httpx import Request, Response
            return Response(200, request=Request("GET", url), json={"models": [
                {"name": "mistral-ins-7b-q4:latest"}, {"name": "qwen3:8b"}, {"name": "qwen3:8b"}]})

    monkeypatch.setattr("backend.qa.httpx.AsyncClient", Client)
    catalog = await question_provider_catalog()
    models = catalog["providers"][0]["models"]
    assert models[0]["id"] == "mistral-ins-7b-q4:latest"
    assert sum(m["id"] == "qwen3:8b" for m in models) == 1
    assert json.dumps(PROVIDERS) == original


@pytest.mark.asyncio
async def test_local_context_keeps_capacity_and_dependencies_with_less_prefill(engine):
    request = GraphQuestion(question="What is Formosa Silicon Supplier B spare capacity?", provider="ollama", model="mistral-ins-7b-q4:latest")
    context, _ = await build_question_context(engine, request)
    compact = compact_local_context(context, request)
    assert len(json.dumps(compact)) < len(json.dumps(context))/2
    records = {s["id"]: s["data"] for s in compact["sources"]}
    assert records["N:B"]["attributes"]["available_capacity"] == 2000
    assert "R:B:SUPPLIES:mcu" in records
    assert "N:mcu" in records
    assert {o["supplier"] for o in records["C:offers"]["offers"]} == {"B"}
    dependency_request = question()
    dependency_request.question = "Which products depend on the MCU?"
    dependency_context, _ = await build_question_context(engine, dependency_request)
    relevant = compact_local_context(dependency_context, dependency_request)
    ids = {s["id"] for s in relevant["sources"]}
    assert {"N:mcu", "N:gateway", "N:controller", "N:monitor", "N:hub"} <= ids
