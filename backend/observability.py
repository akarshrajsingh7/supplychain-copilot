"""LangSmith integration with safe public settings and optional feedback writes.

Tracing keys can ingest runs, but cannot read projects or create feedback. A
failed link lookup or feedback request must never stop business execution.
"""
import asyncio
import os
from uuid import NAMESPACE_URL, UUID, uuid5

from langsmith import Client, get_current_run_tree, traceable
from langsmith.utils import LangSmithConflictError


def enabled(name: str) -> bool:
    return os.getenv(name, "false").lower() in {"true", "1", "yes"}


def public_settings():
    endpoint = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").rstrip("/")
    web_url = os.getenv("LANGSMITH_WEB_URL") or (
        "https://eu.smith.langchain.com" if endpoint == "https://eu.api.smith.langchain.com"
        else "https://smith.langchain.com")
    return {"enabled": enabled("LANGSMITH_TRACING"), "key_configured": bool(os.getenv("LANGSMITH_API_KEY")),
        "project": os.getenv("LANGSMITH_PROJECT", "default"), "endpoint": endpoint, "web_url": web_url,
        "feedback_enabled": enabled("LANGSMITH_PUBLISH_FEEDBACK"),
        "agent_id": os.getenv("LANGSMITH_AGENT_ID"), "agent_environment": os.getenv("LANGSMITH_AGENT_ENVIRONMENT")}


def safe_error(error):
    # Inspect for classification; never expose provider response text or keys.
    message = str(error).lower()
    if "403" in message or "forbidden" in message:
        return "LangSmith denied this operation. Feedback and experiments need a PAT or service key with the required permissions."
    if "401" in message or "unauthorized" in message:
        return "LangSmith authentication failed. Check the API key, workspace and endpoint region."
    return f"LangSmith operation failed ({type(error).__name__}). Check connectivity and configuration."


async def capture_trace(engine, *, resume=False):
    settings = public_settings()
    engine.state["langsmith"].update(settings)
    if not settings["enabled"]:
        return
    run = get_current_run_tree()
    if run is None:
        return
    prefix = "resume_" if resume else ""
    engine.state[prefix+"trace_id"] = str(run.id)
    if not resume:
        engine.state["trace_project"] = run.session_name or settings["project"]
    # A tracing-only key cannot perform the reads SDK URL resolution needs.
    # Optional non-secret IDs permit a direct link without those requests.
    workspace = os.getenv("LANGSMITH_WORKSPACE_ID")
    project = os.getenv("LANGSMITH_PROJECT_ID")
    if workspace and project:
        try:
            UUID(workspace); UUID(project)
            engine.state[prefix+"trace_url"] = f"{settings['web_url']}/o/{workspace}/projects/p/{project}/r/{run.id}?poll=true"
        except ValueError:
            engine.state["langsmith"]["link_message"] = "Workspace/project IDs must be UUIDs for direct links."
    elif enabled("LANGSMITH_RESOLVE_TRACE_URLS"):
        try:
            engine.state[prefix+"trace_url"] = await asyncio.wait_for(asyncio.to_thread(run.get_url), 5)
        except Exception:
            engine.state["langsmith"]["link_message"] = "Trace-link lookup unavailable; open LangSmith and search by trace ID."


@traceable(name="Process evaluations", tags=["process-evaluation", "deterministic-grading"],
           process_inputs=lambda inputs: {"run_id": inputs["report"]["run"]["id"],
                                          "status": inputs["report"]["run"]["status"]})
def trace_process_evaluation(report):
    """Records objective checks in trace outputs; does not claim to publish scores."""
    return {"checks": report["checks"], "summary": report["summary"], "metrics": report["metrics"],
            "checkpoint": report["checkpoint"], "spending_check": report["spending_check"],
            "scope": report["scope"]}


async def publish_feedback(engine, report, *, client=None):
    settings = public_settings()
    if not settings["enabled"] or not settings["key_configured"]:
        return {"state": "disabled", "message": "LangSmith tracing is not configured."}
    if not settings["feedback_enabled"]:
        return {"state": "trace_outputs_only", "message": "Process evaluations are trace outputs. Feedback scores are disabled for the tracing-only setup."}
    trace_id = engine.state.get("trace_id")
    if not trace_id:
        return {"state": "pending", "message": "Run a traced investigation before publishing feedback."}

    def publish():
        sdk = client or Client(timeout_ms=10000)
        sdk.flush(timeout=5)
        # Reading the destination first also detects tracing-only credentials
        # without repeating a forbidden feedback request for every metric.
        project = sdk.read_project(project_name=engine.state.get("trace_project") or settings["project"])
        count = 0
        for check in report["checks"]:
            if check["status"] == "pending":
                continue
            feedback_id = uuid5(NAMESPACE_URL, f"supplychain:{trace_id}:{check['id']}")
            score = int(check["status"] == "pass")
            try:
                sdk.create_feedback(run_id=trace_id, session_id=project.id, key="process_"+check["id"],
                    score=score, comment=check["detail"], feedback_id=feedback_id, stop_after_attempt=1,
                    source_info={"evaluator": "supplychain-process-v1", "event_ids": check["event_ids"]})
            except LangSmithConflictError:
                sdk.update_feedback(feedback_id, score=score, comment=check["detail"])
            count += 1
        return {"state": "published", "count": count, "message": f"Published {count} process scores to the investigation trace."}

    try:
        return await asyncio.to_thread(publish)
    except Exception as error:
        return {"state": "error", "message": safe_error(error)}


def safe_agent_error(error):
    module = type(error).__module__
    if module.startswith("openai"):
        status = getattr(error, "status_code", None)
        if status in (401, 403):
            return "OpenAI rejected the credential or model access. Check OPENAI_API_KEY and OPENAI_MODEL."
        if status == 429:
            return "OpenAI quota or rate limit reached. Check account billing and limits, then retry."
        return f"OpenAI model request failed ({type(error).__name__})."
    text = str(error)
    for name in ["OPENAI_API_KEY", "LANGSMITH_API_KEY", "NEO4J_PASSWORD"]:
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    return text
