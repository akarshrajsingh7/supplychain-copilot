import asyncio
import copy
import inspect
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from langgraph.types import Command, interrupt
from langsmith import trace, traceable
from pydantic import BaseModel
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from backend.agents.commander import build_commander
from backend.config import AGENT_MODE, RUNTIME
from backend.graph.curator import GraphCurator
from backend.models.entities import FactKind, ProposedFact
from backend.models.plans import SourcingPlan
from backend.models.runs import RunPolicy
from backend.observability import capture_trace, public_settings, publish_feedback, safe_agent_error, trace_process_evaluation
from backend.tools.supply_tools import SupplyTools

log = logging.getLogger(__name__)


class IncidentEngine:
    def __init__(self, memory, checkpointer, *, state_path=None, mode=AGENT_MODE, step_delay=None, auto_feedback=True):
        self.memory, self.checkpointer, self.mode = memory, checkpointer, mode
        self.step_delay, self.auto_feedback = step_delay, auto_feedback
        self.tools = SupplyTools()
        self.curator = GraphCurator(memory, self.tools.nodes)
        self.state_path = Path(state_path or RUNTIME / f"incident-{memory.mode}-{mode}.json")
        self.lock = asyncio.Lock()
        self.investigation_lock = asyncio.Lock()
        self.listeners: set[asyncio.Queue] = set()
        self.state = self.fresh_state()
        if self.state_path.exists():
            self.state.update(json.loads(self.state_path.read_text()))
            if self.state["status"] == "INVESTIGATING":
                self.state["status"] = "ERROR"
                self.state["error"] = "Process restarted during investigation. Reset the demo to start a fresh run."
        self.state["langsmith"].update(public_settings())
        self.agent = build_commander(self, checkpointer, mode)
        if self.state["status"] != "HEALTHY" and not self.state.get("model_routing"):
            # Older completed/checkpointed runs used OPENAI_MODEL for every role.
            # Do not label their existing evidence as a routed run after restart.
            self.state["model_routing"] = {**self.model_router.describe(), "policy": "legacy-single", "active": False}

    @staticmethod
    def fresh_state():
        return dict(run_id=str(uuid4()), status="HEALTHY", events=[], impact=None, inventory=None,
                    alternatives=None, plan=None, evaluation=None, rejection=None,
                    rejected=False, verified=False, completed_roles=[], evidence=[], error=None, trace_url=None,
                    policy=RunPolicy().model_dump(), policy_check=None, tool_attempts=0, fault_injected=False,
                    trace_id=None, resume_trace_id=None, trace_project=None, langsmith=public_settings(), process_evaluation=None,
                    model_routing=None, model_calls=[])

    def save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, default=str))
        tmp.replace(self.state_path)

    def config(self):
        return {"configurable": {"thread_id": self.state["run_id"]}, "recursion_limit": 160,
                "run_name": "Supply Chain Commander", "tags": ["simulated", "supplychain", self.mode],
                "metadata": {"incident_id": self.tools.event.id, "demo_run": self.state["run_id"]}}

    async def emit(self, agent, stage, message, data=None, nodes=None, risk=None, *, pause=True):
        event = dict(id=len(self.state["events"])+1, time=datetime.now(timezone.utc).isoformat(),
                     agent=agent, stage=stage, message=message, data=data or {}, nodes=nodes or [], risk=risk)
        self.state["events"].append(event)
        self.save()
        if self.listeners:
            snapshot = copy.deepcopy(self.snapshot())
            for queue in tuple(self.listeners):
                queue.put_nowait(snapshot)
        if pause and stage != "ERROR":
            await asyncio.sleep(self.step_delay if self.step_delay is not None else float(os.getenv("DEMO_STEP_DELAY", ".8")))

    async def read_tool(self, agent, name, operation, inputs=None):
        """Instrument actual read-only calls. Retry transient reads once; never retry writes."""
        call_id = str(uuid4())
        for attempt in (1, 2):
            if self.state["tool_attempts"] >= self.state["policy"]["max_tool_attempts"]:
                await self.emit("Execution guard", "LIMIT", "Read-tool attempt budget exhausted; investigation stopped",
                    self.state["policy"], pause=False)
                raise RuntimeError("Read-tool attempt budget exhausted. Reset to start a new investigation.")
            self.state["tool_attempts"] += 1
            metadata = {"call_id": call_id, "tool": name, "attempt": attempt, "inputs": inputs or {}}
            await self.emit(agent, "TOOL_START", f"Calling {name}", metadata, pause=False)
            started = perf_counter()
            try:
                with trace(name, run_type="tool", inputs=inputs or {}, metadata={"agent": agent,
                    "demo_run": self.state["run_id"], "attempt": attempt, "read_only": True}) as span:
                    if (name == "find_alternative_suppliers" and self.state["policy"]["simulate_tool_failure"]
                            and not self.state["fault_injected"]):
                        self.state["fault_injected"] = True
                        raise ConnectionError("SIMULATED one-time sourcing read failure")
                    result = operation()
                    if inspect.isawaitable(result):
                        result = await result
                    if span is not None:
                        span.end(outputs={"result": result.model_dump(mode="json") if isinstance(result, BaseModel) else result})
            except (ConnectionError, TimeoutError, ServiceUnavailable, SessionExpired) as exc:
                duration = round((perf_counter()-started)*1000, 2)
                await self.emit(agent, "TOOL_ERROR", f"{name} failed ({type(exc).__name__})",
                    {**metadata, "duration_ms": duration, "error_type": type(exc).__name__,
                     "simulated_fault_enabled": self.state["policy"]["simulate_tool_failure"]}, pause=False)
                if attempt == 2:
                    raise
                await self.emit(agent, "RETRY", "Retrying the failed read once; preserving the investigation context", metadata)
                await asyncio.sleep(.05)
                continue
            except Exception as exc:
                await self.emit(agent, "TOOL_ERROR", f"{name} stopped ({type(exc).__name__}); no retry",
                    {**metadata, "duration_ms": round((perf_counter()-started)*1000, 2), "error_type": type(exc).__name__}, pause=False)
                raise
            duration = round((perf_counter()-started)*1000, 2)
            output = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
            output = json.loads(json.dumps(output, default=str))
            await self.emit(agent, "TOOL_RESULT", f"{name} completed in {duration:.0f} ms",
                {**metadata, "duration_ms": duration, "output": output}, pause=False)
            if attempt > 1:
                await self.emit(agent, "RECOVERED", "Read recovered; continuing the same investigation thread", metadata)
            return result

    def snapshot(self):
        return {**self.state, "mode": self.mode, "memory_mode": self.memory.mode,
                "model_routing": self.state.get("model_routing") or self.model_router.describe(),
                "memory_warning": self.memory.warning, "simulated": True,
                "tariff": self.tools.event.model_dump(mode="json")}

    async def finish_observability(self):
        from backend.evaluation import evaluate_run
        try:
            report = await evaluate_run(self)
            trace_process_evaluation(report)
            self.state["process_evaluation"] = report["summary"]
            if self.auto_feedback:
                self.state["langsmith"]["feedback"] = await publish_feedback(self, report)
            await self.emit("Observability", "EVALUATION",
                f"Process checks: {report['summary']['pass']} passed, {report['summary']['pending']} pending, {report['summary']['fail']} failed",
                {"summary": report["summary"], "scope": report["scope"]}, pause=False)
            return {"status": self.state["status"], "run_id": self.state["run_id"],
                    "process_checks": report["checks"], "metrics": report["metrics"]}
        except Exception as error:
            log.warning("Observability finalization failed: %s", type(error).__name__)
            self.state["langsmith"]["message"] = "Observability unavailable; the incident outcome is unchanged."
            self.save()
            return {"status": self.state["status"], "run_id": self.state["run_id"]}

    async def initialize(self):
        await self.memory.seed(self.tools.graph)
        persisted = await self.memory.load_supply_graph()
        if persisted:
            self.tools = SupplyTools(persisted)
            self.curator = GraphCurator(self.memory, self.tools.nodes)
        await self.memory.add_episode("healthy-baseline", {"description": "Healthy synthetic supply chain"},
                                      self.tools.event.reference_time-timedelta(days=1))
        for a in self.tools.baseline_plan().allocations:
            await self.curator.commit_validated_fact(dict(subject=a.supplier, predicate="EFFECTIVE_LANDED_COST",
                object=f"EUR {self.tools.landed(a, tariff=False)[0]:.2f} / {a.component}", source_episode="healthy-baseline",
                confidence=1, reason="Calculated pre-tariff landed cost from seeded offer", kind="OBSERVATION",
                valid_at=self.tools.event.reference_time-timedelta(days=1)))

    async def record(self, role, payload, subject=None, predicate="HAS_EXPOSURE", value=None, kind="INFERENCE", approved=False):
        episode = f"{self.state['run_id']}:{role}"
        # Deterministic event-relative times make the before/current view reproducible.
        start = datetime.fromisoformat(self.state.get("logical_start", self.tools.event.reference_time.isoformat()))
        at = start + timedelta(seconds=len(self.state["events"])+1)
        await self.memory.add_episode(episode, {"agent": role, "kind": kind, "evidence": payload}, at)
        if subject:
            fact = dict(subject=subject, predicate=predicate, object=value or json.dumps(payload, default=str),
                        source_episode=episode, confidence=1, reason=f"Confirmed by deterministic {role} tool",
                        kind=kind, valid_at=at)
            result = await self.curator.commit_validated_fact(fact, human_approved=approved)
            self.state["evidence"].append({**fact, "valid_at": at.isoformat(), "commit": result})
        self.save()

    @traceable(name="Supply Chain Commander · investigation", process_inputs=lambda _: {"operation": "tariff investigation"})
    async def run(self):
        try:
            await capture_trace(self)
            await self.emit("Commander", "PLAN", "Creating investigation plan and delegating specialists")
            result = await self.agent.ainvoke({"messages": [{"role": "user", "content": self.tools.event.description}]}, self.config())
            if result.get("__interrupt__"):
                if self.state["status"] != "AWAITING HUMAN APPROVAL":
                    raise RuntimeError("Unexpected approval interrupt")
            elif self.state["status"] not in {"APPROVED", "REJECTED", "POLICY BLOCKED"}:
                raise RuntimeError("Agent ended before the approval checkpoint")
        except Exception as exc:
            log.error("Investigation failed: %s", type(exc).__name__)
            self.state["status"] = "ERROR"
            self.state["error"] = safe_agent_error(exc)
            await self.emit("System", "ERROR", self.state["error"])
        return await self.finish_observability()

    async def inject(self, policy: RunPolicy | None = None):
        async with self.lock:
            if self.state["status"] != "HEALTHY":
                raise ValueError("Reset the demo before injecting another event")
            self.state["policy"] = (policy or RunPolicy()).model_dump()
            self.state["model_routing"] = self.model_router.describe()
            latest = await self.memory.latest_fact_time()
            start = max(self.tools.event.reference_time, latest+timedelta(seconds=1)) if latest else self.tools.event.reference_time
            self.state["logical_start"] = start.isoformat()
            self.state["status"] = "INVESTIGATING"
            self.save()
            await self.memory.add_episode(self.state["run_id"]+":tariff", self.tools.event.model_dump(mode="json"), self.tools.event.reference_time)
            await self.memory.link_episode_entities(self.state["run_id"]+":tariff",
                [self.tools.event.country, self.tools.event.hs_code] + self.tools.get_downstream_impact()["suppliers"])
            await self.emit("Policy feed", "OBSERVATION", self.tools.event.description, nodes=["A", "D", "mcu", "pmic"], risk="affected")

    async def investigate(self, role):
        # A live model may request multiple tasks together. Serialize operations
        # against the shared incident so planner/critic updates cannot race.
        async with self.investigation_lock:
            return await self._investigate(role)

    async def _investigate(self, role):
        t, s = self.tools, self.state
        labels = {"policy-analyst": "Policy Analyst", "impact-analyst": "Impact Analyst",
                  "inventory-analyst": "Inventory Analyst", "sourcing-analyst": "Sourcing Analyst",
                  "mitigation-planner": "Mitigation Planner", "risk-critic": "Risk / Critic"}
        label = labels[role]
        await self.emit("Commander", "DELEGATE", f"Delegated to {label}")
        if role == "policy-analyst":
            payload = await self.read_tool(label, "parse_policy", lambda: t.event.model_dump(mode="json"))
            await self.record(role, payload, subject="A", kind="OBSERVATION")
            for a in t.baseline_plan().allocations:
                await self.record(role+"-"+a.supplier, payload, subject=a.supplier,
                    predicate="EFFECTIVE_LANDED_COST", value=f"EUR {t.landed(a)[0]:.2f} / {a.component}", kind="OBSERVATION")
            await self.emit(label, "INVESTIGATE", "Confirmed scope: China · HS 8542 · 25% · effective Nov 15", payload)
        elif role == "impact-analyst":
            memory = await self.read_tool(label, "search_graphiti_memory",
                lambda: self.memory.search_graphiti_memory(subject="A"), {"subject": "A"})
            if not memory:
                raise RuntimeError("Policy evidence missing from shared memory")
            impact = await self.read_tool(label, "get_downstream_impact", t.get_downstream_impact)
            exposure = await self.read_tool(label, "calculate_tariff_exposure", t.calculate_tariff_exposure)
            payload = {**impact, **exposure, "memory_evidence": memory}
            s["impact"] = payload
            await self.record(role, payload, subject="mcu")
            await self.emit(label, "INVESTIGATE", "Traversed BOM → plants → orders → customers", payload,
                            payload["downstream"], "affected")
        elif role == "inventory-analyst":
            inventory = [await self.read_tool(label, "get_inventory", lambda: t.get_inventory(c), {"component": c})
                         for c in t.get_downstream_impact()["components"]]
            orders = await self.read_tool(label, "get_open_orders", t.get_open_orders)
            # Existing stock is reserved to high-margin orders first; no allocation is executed.
            reservations = []
            for component in inventory:
                available = component["inventory"]+component["incoming"]
                dependencies = t.get_component_dependencies(component["component"])["orders"]
                for order in orders:
                    if order["id"] in dependencies and available > 0:
                        qty = min(available, order["attributes"]["quantity"])
                        reservations.append(dict(component=component["component"], order=order["id"], quantity=qty))
                        available -= qty
            payload = dict(components=inventory, priority_reservations=reservations)
            s["inventory"] = payload
            await self.record(role, payload)
            mcu = next(c["shortfall"] for c in inventory if c["component"] == "mcu")
            await self.emit(label, "INVESTIGATE", f"Inventory netted against demand; MCU shortfall: {mcu:,} units", payload)
        elif role == "sourcing-analyst":
            payload = {c: await self.read_tool(label, "find_alternative_suppliers",
                lambda: t.find_alternative_suppliers(c), {"component": c}) for c in t.get_downstream_impact()["components"]}
            s["alternatives"] = payload
            await self.record(role+str(s["rejected"]), payload)
            await self.emit(label, "REPLAN" if s["rejected"] else "INVESTIGATE",
                "Evaluating VietCore and a diversified portfolio" if s["rejected"] else "Comparing landed costs for qualified alternatives", payload,
                ["C", "G"] if s["rejected"] else ["B"], "alternative")
        elif role == "mitigation-planner":
            if not s["inventory"] or not s["alternatives"]:
                raise ValueError("Inventory and sourcing investigation required")
            plan = await self.read_tool(label, "build_sourcing_plan",
                t.portfolio_plan if s["rejected"] else t.first_plan, {"revision_after_rejection": s["rejected"]})
            s["plan"] = plan.model_dump()
            s["verified"] = False
            payload = s["plan"]
            await self.record(role+plan.id, payload)
            await self.emit(label, "REPLAN" if s["rejected"] else "PROPOSE", plan.title, payload,
                            sorted({a.supplier for a in plan.allocations if t.nodes[a.supplier].attributes["country"] != t.event.country}), "alternative")
        else:
            if not s["plan"]:
                raise ValueError("No proposed plan")
            plan = SourcingPlan.model_validate(s["plan"])
            evaluation = await self.read_tool(label, "simulate_sourcing_plan", lambda: t.simulate_sourcing_plan(plan),
                {"plan": plan.model_dump()})
            payload = evaluation.model_dump()
            suppliers = {a.supplier for a in plan.allocations}
            payload["capacity_checks"] = []
            for supplier in sorted(suppliers):
                volume = sum(a.quantity for a in plan.allocations if a.supplier == supplier)
                payload["capacity_checks"].append(await self.read_tool(label, "check_supplier_capacity",
                    lambda: t.check_supplier_capacity(supplier, volume), {"supplier": supplier, "requested": volume}))
            s["evaluation"] = payload
            if evaluation.accepted:
                s["verified"] = True
                await self.record(role+plan.id, payload, subject="B", predicate="CAPACITY_VERIFIED", value="PASSED")
                await self.emit(label, "VERIFY", "Validation PASSED · capacity, logistics, deadlines and BOM coverage", payload, ["B", "C", "G"], "alternative")
            else:
                s["rejected"] = True
                s["rejection"] = payload
                await self.record(role+plan.id, payload, subject="B", predicate="CAPACITY_VERIFIED", value="FAILED")
                await self.emit(label, "FAIL", "PLAN REJECTED — " + "; ".join(evaluation.violations), payload, ["B", "cap-B"], "critical")
        if role not in s["completed_roles"]:
            s["completed_roles"].append(role)
        self.save()
        return payload

    async def request_approval(self):
        s = self.state
        if not s["verified"] or not s["rejected"] or len(s["completed_roles"]) != 6:
            raise ValueError("Complete investigation, rejection, replanning and verification first")
        evaluation = self.tools.simulate_sourcing_plan(SourcingPlan.model_validate(s["plan"]))
        if not evaluation.accepted:
            raise ValueError("Plan is no longer feasible")
        limit = s["policy"]["max_incremental_cost_eur"]
        s["policy_check"] = {"allowed": evaluation.incremental_cost <= limit,
            "incremental_cost_eur": evaluation.incremental_cost, "limit_eur": limit,
            "headroom_eur": round(limit-evaluation.incremental_cost, 2)}
        if not s["policy_check"]["allowed"]:
            s["status"] = "POLICY BLOCKED"
            await self.emit("Spending guard", "POLICY BLOCKED",
                f"Approval blocked: €{evaluation.incremental_cost:,.0f} extra cost exceeds the €{limit:,.0f} limit", s["policy_check"])
            return {"decision": "POLICY BLOCKED", "executed_purchase_orders": 0}
        if s["status"] != "AWAITING HUMAN APPROVAL":
            await self.emit("Spending guard", "POLICY PASS", "Verified plan is within the configured extra-cost limit", s["policy_check"])
            s["status"] = "AWAITING HUMAN APPROVAL"
            await self.emit("Commander", "HUMAN APPROVAL", "Verified portfolio ready. Awaiting human approval", evaluation.model_dump())
        decision = interrupt({"plan": s["plan"], "evaluation": evaluation.model_dump(), "allowed": ["APPROVE", "REJECT"]})
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("Invalid human decision")
        if decision == "APPROVE":
            await self.record("human-approval", {"plan": s["plan"], "evaluation": evaluation.model_dump()},
                subject="A", predicate="MITIGATION_DECISION", value=s["plan"]["id"], kind="DECISION", approved=True)
            s["status"] = "APPROVED"
            await self.emit("Human", "APPROVED", "Portfolio approved and recorded in temporal memory", nodes=s["impact"]["orders"], risk="mitigated")
        else:
            await self.record("human-rejection", {"decision": "REJECT", "plan": s["plan"]}, kind="OBSERVATION")
            s["status"] = "REJECTED"
            await self.emit("Human", "REJECTED", "Portfolio rejected. No sourcing actions authorized")
        return {"decision": s["status"], "executed_purchase_orders": 0}

    @traceable(name="Human decision · resume commander", process_inputs=lambda inputs: {"decision": inputs.get("decision")})
    async def decide(self, decision):
        async with self.lock:
            if self.state["status"] != "AWAITING HUMAN APPROVAL":
                raise ValueError("No pending human approval")
            await capture_trace(self, resume=True)
            try:
                await self.agent.ainvoke(Command(resume=decision), self.config())
            except Exception as error:
                self.state["status"] = "ERROR"
                self.state["error"] = safe_agent_error(error)
                await self.emit("System", "ERROR", self.state["error"])
            await self.finish_observability()
            return self.snapshot()

    def graph_view(self, before=False):
        graph = self.tools.graph.model_dump()
        states = {}
        if not before:
            for e in self.state["events"]:
                for n in e["nodes"]:
                    states[n] = e["risk"]
        for n in graph["nodes"]:
            n["risk"] = states.get(n["id"], "healthy")
            n["evidence"] = [f for f in self.state["evidence"] if f["subject"] == n["id"]] if not before else []
            if n["id"] == "A":
                n["attributes"]["effective_mcu_landed_cost"] = 8.5 if before or self.state["status"] == "HEALTHY" else 10.5
        if not before and self.state["status"] != "HEALTHY":
            graph["nodes"].append(dict(id=self.tools.event.id, name="SIMULATED · 25% tariff", type="Policy", risk="critical", attributes=self.tools.event.model_dump(mode="json")))
            for target in ["CN", "8542"]:
                graph["edges"].append(dict(id="tariff:"+target, source=self.tools.event.id, target=target, predicate="APPLIES_TO", attributes={}))
            if self.state["plan"] and self.state["verified"]:
                graph["nodes"].append(dict(id="mitigation", name="Approved portfolio" if self.state["status"] == "APPROVED" else "Proposed portfolio", type="Mitigation", risk="mitigated" if self.state["status"] == "APPROVED" else "alternative", attributes=self.state["evaluation"]))
                for i, a in enumerate(self.state["plan"]["allocations"]):
                    graph["edges"].append(dict(id=f"allocation-{i}", source="mitigation", target=a["supplier"], predicate="ALLOCATES", attributes=a))
        return graph
