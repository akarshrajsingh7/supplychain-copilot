"""Route actual model invocations by task and incident state, with evidence."""
import os
from time import perf_counter
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langsmith import trace

from backend.config import MODEL

THINKING_ROLES = {"mitigation-planner", "risk-critic"}
ROUTINE_ROLES = {"commander", "policy-analyst", "impact-analyst", "inventory-analyst", "sourcing-analyst"}


def supports_reasoning(model):
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


class ModelRouter:
    def __init__(self, engine, mode):
        self.engine, self.mode = engine, mode
        self.policy = os.getenv("MODEL_ROUTING", "cost-aware")
        if self.policy not in {"cost-aware", "single"}:
            raise ValueError("MODEL_ROUTING must be cost-aware or single")
        self.routine = os.getenv("OPENAI_ROUTINE_MODEL") or MODEL
        self.thinking = os.getenv("OPENAI_THINKING_MODEL") or "gpt-5.4"
        self.effort = os.getenv("OPENAI_THINKING_EFFORT", "medium")
        if self.effort not in {"none", "low", "medium", "high", "xhigh"}:
            raise ValueError("OPENAI_THINKING_EFFORT must be none, low, medium, high or xhigh")
        self.models = {}

    def select(self, role):
        if role not in THINKING_ROLES | ROUTINE_ROLES:
            raise ValueError(f"Unknown model-routing role: {role}")
        rejected = bool(self.engine.state.get("rejected"))
        configuration = self.engine.state.get("model_routing") or self.describe()
        if self.mode != "live":
            tier, model, reason = "scripted", "deterministic-demo", "Offline rehearsal; no LLM inference or paid model routing."
        elif configuration["policy"] in {"single", "legacy-single"}:
            tier, model, reason = "single", configuration["single_model"], "Single-model comparison baseline; routing disabled."
        elif role in THINKING_ROLES:
            tier, model, reason = "thinking", configuration["thinking_model"], "Plan synthesis and trade-off review." if role == "mitigation-planner" else "Independent risk review and constraint interpretation."
        elif role == "sourcing-analyst" and rejected:
            tier, model, reason = "thinking", configuration["thinking_model"], "Escalated sourcing after the critic rejected a capacity-infeasible plan."
        else:
            tier, model, reason = "routine", configuration["routine_model"], "Sequential delegation and status tracking." if role == "commander" else "Structured evidence lookup and tool-result reporting."
        return {"role": role, "tier": tier, "model": model, "reason": reason,
                "reasoning_effort": configuration["thinking_effort"] if tier == "thinking" and supports_reasoning(model) else None,
                "after_rejection": rejected}

    def describe(self):
        return {"policy": self.policy, "execution": self.mode, "routine_model": self.routine,
                "thinking_model": self.thinking, "thinking_effort": self.effort,
                "single_model": MODEL, "routine_roles": sorted(ROUTINE_ROLES),
                "thinking_roles": sorted(THINKING_ROLES),
                "escalation": "Sourcing switches to the thinking model after critic rejection.",
                "active": self.mode == "live" and self.policy == "cost-aware"}

    def model_for(self, route):
        from langchain_openai import ChatOpenAI
        key = (route["role"], route["model"], route["reasoning_effort"])
        if key not in self.models:
            kwargs = dict(model=route["model"], timeout=180 if route["tier"] == "thinking" else 60,
                          max_retries=1, tags=["model-routing", route["role"], route["tier"]],
                          metadata={"routing_role": route["role"], "routing_tier": route["tier"]})
            # Reasoning models can reject temperature=0. Leave their sampling default.
            if supports_reasoning(route["model"]):
                # GPT-5.4 reasoning + function tools needs the Responses API.
                kwargs.update(use_responses_api=True, output_version="responses/v1")
                if route["reasoning_effort"]:
                    kwargs["reasoning_effort"] = route["reasoning_effort"]
            else:
                kwargs["temperature"] = 0
            self.models[key] = ChatOpenAI(**kwargs)
        return self.models[key]


class ModelRoutingMiddleware(AgentMiddleware):
    def __init__(self, router, role):
        self.router, self.role = router, role

    async def awrap_model_call(self, request, handler):
        router, engine = self.router, self.router.engine
        route = router.select(self.role)
        # Scripted models still execute through the middleware, without suggesting
        # that a paid model was selected or a routing benefit was measured.
        selected = router.model_for(route) if router.mode == "live" else request.model
        record = {**route, "id": str(uuid4()), "status": "started", "duration_ms": None, "usage": None}
        overrides = {"model": selected}
        state = engine.state
        if (router.mode == "live" and self.role == "commander" and state.get("status") == "INVESTIGATING"
                and state.get("verified") and state.get("rejected") and len(state.get("completed_roles", [])) == 6):
            # Once the verified revision is complete, constrain the coordinator
            # to the approval-request tool. This requests a human interrupt; it
            # does not approve the plan or bypass the engine's independent gate.
            name = "request_mitigation_approval"
            overrides["tool_choice"] = {"type": "function", "function": {"name": name}}
            record["required_tool"] = name
        engine.state.setdefault("model_calls", []).append(record)
        await engine.emit("Model router", "MODEL_ROUTE", f"{self.role} → {route['model']} · {route['tier']}",
                          record.copy(), pause=False)
        started = perf_counter()
        try:
            with trace("Model route · "+self.role, inputs=route,
                       metadata={**route, "demo_run": engine.state["run_id"]}) as span:
                result = await handler(request.override(**overrides))
                response = getattr(result, "model_response", result)
                record["tool_calls"] = [call["name"] for message in response.result
                                        for call in getattr(message, "tool_calls", [])]
                usages = [getattr(message, "usage_metadata", None) for message in response.result]
                usages = [usage for usage in usages if usage]
                record.update(status="success", duration_ms=round((perf_counter()-started)*1000, 2),
                              usage={k: sum(u.get(k, 0) for u in usages)
                                     for k in ("input_tokens", "output_tokens", "total_tokens")} if usages else None)
                if span is not None:
                    span.end(outputs=record.copy())
        except Exception as error:
            record.update(status="error", error_type=type(error).__name__, duration_ms=round((perf_counter()-started)*1000, 2))
            await engine.emit("Model router", "MODEL_ERROR", f"{self.role} model call failed; no silent model fallback.", record.copy(), pause=False)
            raise
        await engine.emit("Model router", "MODEL_RESULT", f"{self.role} completed with {route['model']}", record.copy(), pause=False)
        return result
