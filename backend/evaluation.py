"""Evidence-based process checks. These are not a self-assigned hackathon score."""
import copy
from collections import Counter, defaultdict
from datetime import datetime, timezone

from backend.agents.commander import ROLES


async def evaluate_run(engine, *, running=False):
    state = copy.deepcopy(engine.snapshot())
    events = state["events"]
    results = [e for e in events if e["stage"] == "TOOL_RESULT"]
    starts = [e for e in events if e["stage"] == "TOOL_START"]
    errors = [e for e in events if e["stage"] == "TOOL_ERROR"]
    ready = state["status"] in {"AWAITING HUMAN APPROVAL", "APPROVED", "REJECTED", "POLICY BLOCKED", "ERROR"} and not running
    checks = []

    def check(id, name, passed, detail, event_ids=None, *, exercised=True):
        checks.append({"id": id, "name": name,
            "status": "pending" if not exercised else "pass" if passed else "fail",
            "detail": detail, "event_ids": event_ids or []})

    roles_with_results = {e["agent"] for e in results}
    expected = {r[1] for r in ROLES}
    check("specialists", "All six specialists executed tools", expected <= roles_with_results,
        f"{len(expected & roles_with_results)}/6 specialist roles have successful tool results.",
        [e["id"] for e in results], exercised=ready)
    bad_capacity = [e for e in results if e["data"]["tool"] == "check_supplier_capacity"
        and e["data"]["output"]["requested"] > e["data"]["output"]["available"]
        and not e["data"]["output"]["feasible"]]
    check("capacity_rejection", "Critic caught an infeasible allocation", bool(bad_capacity) and state["rejected"],
        "Requested and available volumes are preserved in the capacity-check output.",
        [e["id"] for e in bad_capacity], exercised=ready)
    positions = {stage: [i for i, e in enumerate(events) if e["stage"] == stage]
                 for stage in ["PROPOSE", "FAIL", "REPLAN", "VERIFY"]}
    ordered = any(p < f < r < v for p in positions["PROPOSE"] for f in positions["FAIL"]
                  for r in positions["REPLAN"] for v in positions["VERIFY"])
    check("replan", "Replanning followed rejection and ended in verification", ordered,
        "Checks the event order PROPOSE → FAIL → REPLAN → VERIFY.",
        [e["id"] for e in events if e["stage"] in positions], exercised=ready)
    plan = state["plan"]
    totals = defaultdict(int)
    if plan:
        for allocation in plan["allocations"]:
            totals[allocation["supplier"]] += allocation["quantity"]
    feasible = all(q <= engine.tools.nodes[s].attributes["available_capacity"] for s, q in totals.items())
    critic_verified = any(e["data"]["tool"] == "simulate_sourcing_plan"
        and e["agent"] == "Risk / Critic" and e["data"]["inputs"]["plan"]["id"] == plan["id"]
        and e["data"]["output"]["accepted"] for e in results) if plan else False
    check("verified_portfolio", "Revised plan respects aggregate supplier capacity", bool(plan) and feasible and critic_verified,
        "Aggregates every allocation row per supplier and requires the Critic's successful simulation.",
        [e["id"] for e in results if e["data"]["tool"] == "simulate_sourcing_plan"], exercised=ready)
    spending = state["policy_check"]
    spending_honored = spending and (
        spending["allowed"] and spending["incremental_cost_eur"] <= spending["limit_eur"] or
        not spending["allowed"] and state["status"] == "POLICY BLOCKED")
    check("spending", "Configured extra-cost limit was enforced", bool(spending_honored),
        "Approval must be blocked when modeled incremental procurement cost exceeds the limit.",
        [e["id"] for e in events if e["stage"] in {"POLICY PASS", "POLICY BLOCKED"}], exercised=bool(spending))
    decisions = [f for f in state["evidence"] if f["kind"] == "DECISION"]
    decision_safe = (all(f["source_episode"].endswith(":human-approval") for f in decisions)
        and any(e["agent"] == "Human" and e["stage"] == "APPROVED" for e in events)) if decisions else state["status"] != "APPROVED"
    check("decision_gate", "Decision memory respects the human approval gate", decision_safe,
        f"{len(decisions)} decision fact(s); status is {state['status']}. No purchase orders are executed by this prototype.",
        [e["id"] for e in events if e["agent"] == "Human"], exercised=ready)
    checkpoint = await engine.agent.aget_state(engine.config())
    interrupted = bool(checkpoint.interrupts)
    if ready and state["status"] in {"APPROVED", "REJECTED"}:
        async for past in engine.agent.aget_state_history(engine.config(), limit=25):
            if past.interrupts:
                interrupted = True
                break
    checkpoint_info = {"backend": type(engine.checkpointer).__name__, "thread_id": state["run_id"],
        "available": bool(checkpoint.created_at), "next_nodes": list(checkpoint.next),
        "pending_interrupts": len(checkpoint.interrupts), "approval_interrupt_observed": interrupted}
    check("checkpoint", "Human approval used a real resumable interrupt", interrupted,
        f"Checkpoint backend: {checkpoint_info['backend']}; same incident thread ID is used on resume.",
        [e["id"] for e in events if e["stage"] == "HUMAN APPROVAL"],
        exercised=ready and state["status"] in {"AWAITING HUMAN APPROVAL", "APPROVED", "REJECTED"})
    provenance = all(engine.memory.has_episode(f["source_episode"]) for f in state["evidence"])
    check("provenance", "Curated evidence references existing source episodes", bool(state["evidence"]) and provenance,
        f"{len(state['evidence'])} fact(s) checked against the episode journal.", exercised=ready)
    retry_events = [e for e in events if e["stage"] == "RETRY"]
    recovered = [e for e in events if e["stage"] == "RECOVERED"]
    recovered_ids = {e["data"]["call_id"] for e in recovered}
    check("transient_recovery", "Injected read failure recovered within the same run", bool(retry_events) and
        all(e["data"]["call_id"] in recovered_ids for e in retry_events),
        "Enable Test recovery before injection to exercise one real exception and a bounded read retry.",
        [e["id"] for e in events if e["stage"] in {"TOOL_ERROR", "RETRY", "RECOVERED"}],
        exercised=ready and state["policy"]["simulate_tool_failure"])
    attempts_by_call = Counter(e["data"]["call_id"] for e in starts)
    check("bounded_work", "Read-tool attempts stayed within execution limits",
        len(starts) <= state["policy"]["max_tool_attempts"] and all(n <= 2 for n in attempts_by_call.values()),
        f"{len(starts)}/{state['policy']['max_tool_attempts']} instrumented read attempts; at most two per call.",
        [e["id"] for e in events if e["stage"] == "LIMIT"], exercised=bool(starts))
    counts = Counter(c["status"] for c in checks)
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": {"id": state["run_id"], "status": state["status"], "agent_mode": engine.mode,
            "model_execution": "Scripted BaseChatModel; no incident LLM inference" if engine.mode == "deterministic" else "ChatOpenAI live-model configuration",
            "memory_backend": engine.memory.mode, "simulated": True, "policy": state["policy"]},
        "summary": {status: counts[status] for status in ["pass", "pending", "fail"]},
        "metrics": {"read_tool_attempts": len(starts), "successful_reads": len(results), "failed_reads": len(errors),
            "retries": len(retry_events), "tool_execution_ms": round(sum(e["data"]["duration_ms"] for e in results+errors), 2),
            "roles_with_successful_tools": len(expected & roles_with_results)},
        "checks": checks, "checkpoint": checkpoint_info,
        "plan": plan, "evaluation": state["evaluation"], "spending_check": spending,
        "provenance": state["evidence"], "trace_url": state["trace_url"], "events": events,
        "trace_id": state.get("trace_id"), "resume_trace_id": state.get("resume_trace_id"),
        "langsmith": state.get("langsmith", {}), "process_evaluation": state.get("process_evaluation"),
        "model_routing": state.get("model_routing") or engine.model_router.describe(),
        "model_calls": state.get("model_calls", []),
        "scope": "Process checks from actual run records, not an LLM-quality evaluation or predicted judging score. Q&A is independent and excluded."}
