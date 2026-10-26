import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from langgraph.types import Command, interrupt
from langsmith import traceable

from backend.agents.commander import build_commander
from backend.config import AGENT_MODE, RUNTIME
from backend.graph.curator import GraphCurator
from backend.models.entities import FactKind, ProposedFact
from backend.models.plans import SourcingPlan
from backend.tools.supply_tools import SupplyTools

log = logging.getLogger(__name__)


class IncidentEngine:
    def __init__(self, memory, checkpointer, *, state_path=None, mode=AGENT_MODE):
        self.memory, self.checkpointer, self.mode = memory, checkpointer, mode
        self.tools = SupplyTools()
        self.curator = GraphCurator(memory, self.tools.nodes)
        self.state_path = Path(state_path or RUNTIME / f"incident-{memory.mode}-{mode}.json")
        self.lock = asyncio.Lock()
        self.listeners: set[asyncio.Queue] = set()
        self.state = self.fresh_state()
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if self.state["status"] == "INVESTIGATING":
                self.state["status"] = "ERROR"
                self.state["error"] = "Process restarted during investigation. Reset the demo to start a fresh run."
        self.agent = build_commander(self, checkpointer, mode)

    @staticmethod
    def fresh_state():
        return dict(run_id=str(uuid4()), status="HEALTHY", events=[], impact=None, inventory=None,
                    alternatives=None, plan=None, evaluation=None, rejection=None,
                    rejected=False, verified=False, completed_roles=[], evidence=[], error=None, trace_url=None)

    def save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, default=str))
        tmp.replace(self.state_path)

    def config(self):
        return {"configurable": {"thread_id": self.state["run_id"]}, "recursion_limit": 160,
                "run_name": "Supply Chain Commander", "tags": ["simulated", "supplychain", self.mode],
                "metadata": {"incident_id": self.tools.event.id, "demo_run": self.state["run_id"]}}

    async def emit(self, agent, stage, message, data=None, nodes=None, risk=None):
        event = dict(id=len(self.state["events"])+1, time=datetime.now(timezone.utc).isoformat(),
                     agent=agent, stage=stage, message=message, data=data or {}, nodes=nodes or [], risk=risk)
        self.state["events"].append(event)
        self.save()
        for queue in tuple(self.listeners):
            queue.put_nowait(self.snapshot())
        if stage != "ERROR":
            await asyncio.sleep(float(os.getenv("DEMO_STEP_DELAY", ".8")))

    def snapshot(self):
        return {**self.state, "mode": self.mode, "memory_mode": self.memory.mode,
                "memory_warning": self.memory.warning, "simulated": True,
                "tariff": self.tools.event.model_dump(mode="json")}

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

    @traceable(name="Supply Chain Commander · investigation")
    async def run(self):
        try:
            from langsmith import get_current_run_tree
            trace = get_current_run_tree()
            if trace and os.getenv("LANGSMITH_TRACING", "false").lower() == "true":
                self.state["trace_url"] = trace.get_url()
            await self.emit("Commander", "PLAN", "Creating investigation plan and delegating specialists")
            result = await self.agent.ainvoke({"messages": [{"role": "user", "content": self.tools.event.description}]}, self.config())
            if result.get("__interrupt__"):
                if self.state["status"] != "AWAITING HUMAN APPROVAL":
                    raise RuntimeError("Unexpected approval interrupt")
            elif self.state["status"] not in {"APPROVED", "REJECTED"}:
                raise RuntimeError("Agent ended before the approval checkpoint")
        except Exception as exc:
            log.exception("Investigation failed")
            self.state["status"] = "ERROR"
            self.state["error"] = str(exc)
            await self.emit("System", "ERROR", str(exc))

    async def inject(self):
        async with self.lock:
            if self.state["status"] != "HEALTHY":
                raise ValueError("Reset the demo before injecting another event")
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
        t, s = self.tools, self.state
        labels = {"policy-analyst": "Policy Analyst", "impact-analyst": "Impact Analyst",
                  "inventory-analyst": "Inventory Analyst", "sourcing-analyst": "Sourcing Analyst",
                  "mitigation-planner": "Mitigation Planner", "risk-critic": "Risk / Critic"}
        label = labels[role]
        await self.emit("Commander", "DELEGATE", f"Delegated to {label}")
        if role == "policy-analyst":
            payload = t.event.model_dump(mode="json")
            await self.record(role, payload, subject="A", kind="OBSERVATION")
            for a in t.baseline_plan().allocations:
                await self.record(role+"-"+a.supplier, payload, subject=a.supplier,
                    predicate="EFFECTIVE_LANDED_COST", value=f"EUR {t.landed(a)[0]:.2f} / {a.component}", kind="OBSERVATION")
            await self.emit(label, "INVESTIGATE", "Confirmed scope: China · HS 8542 · 25% · effective Nov 15", payload)
        elif role == "impact-analyst":
            memory = await self.memory.search_graphiti_memory(subject="A")
            if not memory:
                raise RuntimeError("Policy evidence missing from shared memory")
            payload = {**t.get_downstream_impact(), **t.calculate_tariff_exposure(), "memory_evidence": memory}
            s["impact"] = payload
            await self.record(role, payload, subject="mcu")
            await self.emit(label, "INVESTIGATE", "Traversed BOM → plants → orders → customers", payload,
                            payload["downstream"], "affected")
        elif role == "inventory-analyst":
            inventory = [t.get_inventory(c) for c in t.get_downstream_impact()["components"]]
            # Existing stock is reserved to high-margin orders first; no allocation is executed.
            reservations = []
            for component in inventory:
                available = component["inventory"]+component["incoming"]
                dependencies = t.get_component_dependencies(component["component"])["orders"]
                for order in t.get_open_orders():
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
            payload = {c: t.find_alternative_suppliers(c) for c in t.get_downstream_impact()["components"]}
            s["alternatives"] = payload
            await self.record(role+str(s["rejected"]), payload)
            await self.emit(label, "REPLAN" if s["rejected"] else "INVESTIGATE",
                "Evaluating VietCore and a diversified portfolio" if s["rejected"] else "Comparing landed costs for qualified alternatives", payload,
                ["C", "G"] if s["rejected"] else ["B"], "alternative")
        elif role == "mitigation-planner":
            if not s["inventory"] or not s["alternatives"]:
                raise ValueError("Inventory and sourcing investigation required")
            plan = t.portfolio_plan() if s["rejected"] else t.first_plan()
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
            evaluation = t.simulate_sourcing_plan(plan)
            payload = evaluation.model_dump()
            suppliers = {a.supplier for a in plan.allocations}
            payload["capacity_checks"] = [t.check_supplier_capacity(supplier,
                sum(a.quantity for a in plan.allocations if a.supplier == supplier)) for supplier in sorted(suppliers)]
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
        if s["status"] != "AWAITING HUMAN APPROVAL":
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

    @traceable(name="Human decision · resume commander")
    async def decide(self, decision):
        async with self.lock:
            if self.state["status"] != "AWAITING HUMAN APPROVAL":
                raise ValueError("No pending human approval")
            result = await self.agent.ainvoke(Command(resume=decision), self.config())
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
