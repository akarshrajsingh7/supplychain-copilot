import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from backend.config import MEMORY_MODE, RUNTIME
from backend.engine import IncidentEngine
from backend.graph.graphiti_client import TemporalMemory
from backend.models.questions import GraphAnswer, GraphQuestion
from backend.models.runs import RunPolicy
from backend.evaluation import evaluate_run
from backend.observability import public_settings, publish_feedback
from backend.qa import QuestionError, answer_graph_question, question_provider_catalog

logging.basicConfig(level=logging.INFO)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    memory = TemporalMemory(MEMORY_MODE)
    await memory.connect()
    async with AsyncSqliteSaver.from_conn_string(str(RUNTIME / "checkpoints.sqlite")) as saver:
        await saver.setup()
        app.state.engine = IncidentEngine(memory, saver)
        await app.state.engine.initialize()
        app.state.task = None
        yield
        if app.state.task and not app.state.task.done():
            app.state.task.cancel()
            try:
                await app.state.task
            except asyncio.CancelledError:
                pass
    await memory.close()


app = FastAPI(title="SupplyChain Copilot", lifespan=lifespan)


@app.get("/api/qa/models")
async def question_models():
    return await question_provider_catalog()


@app.post("/api/qa/ask", response_model=GraphAnswer)
async def ask_graph(body: GraphQuestion, request: Request,
                    api_key: str | None = Header(default=None, alias="X-LLM-API-Key")):
    try:
        answer = await answer_graph_question(request.app.state.engine, body, api_key)
        return answer
    except QuestionError as exc:
        raise HTTPException(exc.status, exc.message) from None


@app.get("/api/health")
def health(request: Request):
    e = request.app.state.engine
    return {"ok": True, "agent_mode": e.mode, "memory_mode": e.memory.mode, "graphiti_connected": e.memory.client is not None}


@app.get("/api/observability")
def observability(request: Request):
    engine = request.app.state.engine
    return {"langsmith": public_settings(), "agent_mode": engine.mode,
            "model_routing": engine.model_router.describe(),
            "trace_id": engine.state.get("trace_id"), "feedback": engine.state.get("langsmith", {}).get("feedback")}


@app.get("/api/state")
def state(request: Request):
    return request.app.state.engine.snapshot()


@app.get("/api/evaluation")
async def run_evaluation(request: Request, download: bool = False):
    task = request.app.state.task
    report = await evaluate_run(request.app.state.engine, running=bool(task and not task.done()))
    headers = {"Content-Disposition": f'attachment; filename="supplychain-run-{report["run"]["id"]}.json"'} if download else {}
    return JSONResponse(report, headers=headers)


@app.post("/api/evaluation/publish")
async def publish_evaluation(request: Request):
    engine = request.app.state.engine
    task = request.app.state.task
    report = await evaluate_run(engine, running=bool(task and not task.done()))
    result = await publish_feedback(engine, report)
    engine.state["langsmith"]["feedback"] = result
    engine.save()
    return result


@app.get("/api/graph")
def graph(request: Request, view: Literal["before", "current"] = "current"):
    return request.app.state.engine.graph_view(view == "before")


@app.get("/api/memory")
async def memory(request: Request, subject: str | None = None, as_of: str | None = None):
    try:
        return await request.app.state.engine.memory.search_graphiti_memory(subject=subject, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/incidents")
async def inject(request: Request, options: RunPolicy | None = None):
    engine = request.app.state.engine
    try:
        await engine.inject(options)
        request.app.state.task = asyncio.create_task(engine.run())
        return engine.snapshot()
    except ValueError as exc:
        raise HTTPException(409, str(exc))


class Decision(BaseModel):
    decision: Literal["APPROVE", "REJECT"]


@app.post("/api/decision")
async def decision(body: Decision, request: Request):
    try:
        task = request.app.state.task
        if task and not task.done():
            await task  # Ensure the interrupt checkpoint has actually been persisted.
        return await request.app.state.engine.decide(body.decision)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/reset")
async def reset(request: Request):
    engine = request.app.state.engine
    async with engine.lock:
        task = request.app.state.task
        if task and not task.done():
            raise HTTPException(409, "Investigation is still running")
        engine.state = engine.fresh_state()
        engine.save()
        await engine.emit("System", "READY", "Healthy graph restored. Historical memory retained")
    return engine.snapshot()


@app.get("/api/events")
async def events(request: Request):
    engine = request.app.state.engine
    queue = asyncio.Queue()
    engine.listeners.add(queue)

    async def stream():
        try:
            yield "data: " + json.dumps(engine.snapshot(), default=str) + "\n\n"
            while not await request.is_disconnected():
                try:
                    snapshot = await asyncio.wait_for(queue.get(), 15)
                    yield "data: " + json.dumps(snapshot, default=str) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            engine.listeners.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
