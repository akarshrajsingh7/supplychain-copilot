"""Offline tool-calling fixture for the *real* Deep Agents runtime.

No LLM inference is claimed in deterministic mode. Delegation, middleware, tools,
checkpoints, interrupts, and traces still execute through the installed harness.
"""
from typing import Any
from uuid import uuid4
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


STEPS = [
    ("write_todos", {"todos": [{"content": s, "status": "pending"} for s in [
        "Interpret policy and trace exposure", "Inspect inventory and alternatives",
        "Propose and critique sourcing", "Replan and verify constraints", "Request human approval"]]}),
    ("task", {"subagent_type": "policy-analyst", "description": "Interpret the tariff scope; use parse_policy."}),
    ("task", {"subagent_type": "impact-analyst", "description": "Traverse downstream exposure and search shared memory."}),
    ("task", {"subagent_type": "inventory-analyst", "description": "Calculate inventory shortfalls and priority orders."}),
    ("task", {"subagent_type": "sourcing-analyst", "description": "Evaluate qualified supplier alternatives and landed costs."}),
    ("task", {"subagent_type": "mitigation-planner", "description": "Generate the initial lowest-price MCU migration proposal."}),
    ("task", {"subagent_type": "risk-critic", "description": "Independently validate the proposed plan; reject infeasible capacity."}),
    ("task", {"subagent_type": "sourcing-analyst", "description": "Investigate the diversified portfolio after rejection."}),
    ("task", {"subagent_type": "mitigation-planner", "description": "Revise the rejected proposal using a capacity-constrained portfolio."}),
    ("task", {"subagent_type": "risk-critic", "description": "Verify the revised plan against all constraints."}),
    ("request_mitigation_approval", {}),
    ("write_todos", {"todos": [{"content": s, "status": "completed"} for s in [
        "Interpret policy and trace exposure", "Inspect inventory and alternatives",
        "Propose and critique sourcing", "Replan and verify constraints", "Request human approval"]]}),
]


class DeterministicDemoModel(BaseChatModel):
    role: str = "commander"
    tool_name: str = ""

    @property
    def _llm_type(self):
        return "deterministic-demo-tool-calling"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        count = sum(1 for m in messages if isinstance(m, AIMessage) and m.tool_calls)
        steps = STEPS if self.role == "commander" else [(self.tool_name, {})]
        if count < len(steps):
            name, args = steps[count]
            message = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": str(uuid4()), "type": "tool_call"}])
        else:
            message = AIMessage(content="Investigation complete. See the structured tool evidence and human decision.")
        return ChatResult(generations=[ChatGeneration(message=message)])
