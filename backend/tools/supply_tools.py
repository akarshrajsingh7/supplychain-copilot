"""Controlled graph queries and deterministic calculations. Never accept agent Cypher."""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from langsmith import traceable

from backend.config import DATA
from backend.models.entities import SupplyGraph
from backend.models.events import TariffEvent
from backend.models.plans import Allocation, PlanEvaluation, SourcingPlan
from backend.tools.calculation_tools import calculate_landed_cost, calculate_shortfall, money


class SupplyTools:
    def __init__(self, graph: SupplyGraph | None = None):
        self.graph = graph or SupplyGraph.model_validate_json((DATA / "seed_supply_chain.json").read_text())
        self.event = TariffEvent.model_validate_json((DATA / "tariff_event.json").read_text())
        self.nodes = {n.id: n for n in self.graph.nodes}

    def query_supply_graph(self, entity_type: str | None = None) -> list[dict]:
        return [n.model_dump() for n in self.graph.nodes if not entity_type or n.type == entity_type]

    def edges(self, predicate: str, source: str | None = None, target: str | None = None):
        return [e for e in self.graph.edges if e.predicate == predicate
                and (source is None or e.source == source) and (target is None or e.target == target)]

    def get_supplier_details(self, supplier: str) -> dict:
        n = self.nodes[supplier]
        if n.type != "Supplier":
            raise ValueError("Expected supplier")
        return {**n.model_dump(), "offers": [e.model_dump() for e in self.edges("SUPPLIES", supplier)]}

    def get_component_dependencies(self, component: str) -> dict:
        products = [e.source for e in self.edges("REQUIRES", target=component)]
        orders = [e.source for e in self.edges("CONTAINS") if e.target in products]
        return {"component": component, "products": products, "orders": orders}

    @traceable(name="Impact Analyst · downstream traversal", run_type="tool")
    def get_downstream_impact(self) -> dict:
        affected_suppliers, components = set(), set()
        for e in self.edges("SUPPLIES"):
            if (self.nodes[e.source].attributes["country"] == self.event.country
                    and self.nodes[e.target].attributes["hs_code"] == self.event.hs_code):
                affected_suppliers.add(e.source)
                components.add(e.target)
        products = {e.source for e in self.edges("REQUIRES") if e.target in components}
        plants = {e.source for e in self.edges("PRODUCES") if e.target in products}
        orders = {e.source for e in self.edges("CONTAINS") if e.target in products}
        customers = {e.target for e in self.edges("FOR_CUSTOMER") if e.source in orders}
        return {"suppliers": sorted(affected_suppliers), "components": sorted(components),
                "products": sorted(products), "plants": sorted(plants), "orders": sorted(orders),
                "customers": sorted(customers), "direct": sorted(affected_suppliers | components),
                "downstream": sorted(products | plants | orders | customers),
                "order_value": sum(self.nodes[o].attributes["order_value"] for o in orders)}

    def get_open_orders(self) -> list[dict]:
        return sorted(self.query_supply_graph("Order"), key=lambda n: (-n["attributes"]["gross_margin"], n["attributes"]["deadline"]))

    def get_inventory(self, component: str) -> dict:
        deadlines = [self.nodes[o].attributes["deadline"] for o in self.get_component_dependencies(component)["orders"]]
        cutoff = min(deadlines, default="2026-11-25")
        inventory = sum(e.attributes["quantity"] for e in self.edges("STOCKS", target=component))
        incoming = sum(e.attributes["quantity"] for e in self.edges("IN_TRANSIT", target=component)
                       if e.attributes["arrival"] <= cutoff)
        demand = sum(self.nodes[e.source].attributes["quantity"] * bom.attributes["bom_quantity"]
                     for bom in self.edges("REQUIRES", target=component)
                     for e in self.edges("CONTAINS", target=bom.source))
        return dict(component=component, demand=demand, inventory=inventory, incoming=incoming,
                    shortfall=calculate_shortfall(demand, inventory, incoming))

    def offer(self, supplier: str, component: str):
        offers = self.edges("SUPPLIES", supplier, component)
        if not offers:
            raise ValueError(f"{supplier} is not qualified for {component}")
        return offers[0].attributes

    def check_supplier_capacity(self, supplier: str, requested: int) -> dict:
        available = self.nodes[supplier].attributes["available_capacity"]
        return dict(supplier=supplier, requested=requested, available=available, feasible=requested <= available)

    def check_logistics_risk(self, supplier: str) -> dict:
        ports = [self.nodes[e.target] for e in self.edges("SHIPS_THROUGH", supplier)]
        delay = max((p.attributes["delay_days"] for p in ports), default=0)
        return dict(supplier=supplier, ports=[p.name for p in ports], delay_days=delay,
                    lead_days=self.nodes[supplier].attributes["lead_days"] + delay)

    def landed(self, a: Allocation, tariff=True) -> tuple[float, float]:
        offer = self.offer(a.supplier, a.component)
        affected = (self.nodes[a.supplier].attributes["country"] == self.event.country
                    and self.nodes[a.component].attributes["hs_code"] == self.event.hs_code)
        rate = self.event.rate if affected and tariff and not a.expedited else 0
        landed = calculate_landed_cost(offer["unit_price"], offer["freight"], rate,
                                       offer.get("expedite_premium", .5) if a.expedited else 0)
        return landed, money(offer["unit_price"] * rate * a.quantity)

    def find_alternative_suppliers(self, component: str) -> list[dict]:
        return sorted([dict(name=self.nodes[e.source].name,
                            landed_cost=self.landed(Allocation(supplier=e.source, component=component, quantity=1))[0],
                            **self.check_supplier_capacity(e.source, 0),
                            logistics=self.check_logistics_risk(e.source))
                       for e in self.edges("SUPPLIES", target=component)], key=lambda x: x["landed_cost"])

    def baseline_plan(self):
        allocations = []
        impact = self.get_downstream_impact()
        for c in impact["components"]:
            s = next(e.source for e in self.edges("SUPPLIES", target=c) if e.source in impact["suppliers"])
            allocations.append(Allocation(supplier=s, component=c, quantity=self.get_inventory(c)["shortfall"]))
        return SourcingPlan(id="baseline", title="Unmitigated tariff scenario", allocations=allocations)

    def first_plan(self):
        baseline = self.baseline_plan()
        return SourcingPlan(id="plan-1", title="Migrate MCU demand to Formosa Silicon",
                           allocations=[a.model_copy(update={"supplier": "B"}) if a.component == "mcu" else a for a in baseline.allocations])

    def portfolio_plan(self):
        baseline = self.baseline_plan()
        allocations = []
        for a in baseline.allocations:
            if a.component != "mcu":
                # Independent qualified PMIC source; its real spare capacity is checked by the critic.
                allocations.append(Allocation(supplier="G", component=a.component, quantity=a.quantity))
                continue
            b = min(int(a.quantity * .4), self.nodes["B"].attributes["available_capacity"])
            c = min(int(a.quantity * .3), self.nodes["C"].attributes["available_capacity"])
            remaining = a.quantity - b - c
            expedited = min(remaining, self.nodes["A"].attributes["pre_tariff_capacity"])
            for supplier, qty, early in [("B", b, False), ("C", c, False), ("A", expedited, True), ("A", remaining-expedited, False)]:
                if qty:
                    allocations.append(Allocation(supplier=supplier, component=a.component, quantity=qty, expedited=early))
        return SourcingPlan(id="plan-2", title="Diversify, expedite & protect priority orders", allocations=allocations)

    @traceable(name="Critic · simulate sourcing plan", run_type="tool")
    def simulate_sourcing_plan(self, plan: SourcingPlan) -> PlanEvaluation:
        baseline = self.baseline_plan()
        baseline_cost = sum(self.landed(a)[0]*a.quantity for a in baseline.allocations)
        pre_cost = sum(self.landed(a, tariff=False)[0]*a.quantity for a in baseline.allocations)
        baseline_tariff = sum(self.landed(a)[1] for a in baseline.allocations)
        violations, risks = [], []
        supplier_totals, component_totals, early_totals = defaultdict(int), defaultdict(int), defaultdict(int)
        cost, residual_tariff = 0., 0.
        for a in plan.allocations:
            supplier_totals[a.supplier] += a.quantity
            component_totals[a.component] += a.quantity
            try:
                landed, tariff = self.landed(a)
                cost += landed*a.quantity
                residual_tariff += tariff
                supplier = self.nodes[a.supplier]
                if not supplier.attributes["qualified"] or not self.offer(a.supplier, a.component)["qualified"]:
                    violations.append(f"{a.supplier}: supplier/component not qualified")
                logistics = self.check_logistics_risk(a.supplier)
                lead = supplier.attributes.get("expedite_lead_days", logistics["lead_days"]) if a.expedited else logistics["lead_days"]
                arrival = self.event.reference_time + timedelta(days=lead)
                if not a.expedited:
                    # Regular replenishment is booked for Nov 17; alternatives can arrive earlier.
                    booked = supplier.attributes.get("booked_arrival")
                    if booked:
                        arrival = max(arrival, datetime.fromisoformat(booked))
                if a.expedited:
                    early_totals[a.supplier] += a.quantity
                    if arrival >= self.event.effective_at:
                        violations.append(f"{a.supplier}: expedited stock arrives after tariff activates")
                for o in self.get_component_dependencies(a.component)["orders"]:
                    order = self.nodes[o].attributes
                    deadline = datetime.fromisoformat(order["deadline"]+"T23:59:59+00:00")
                    if arrival + timedelta(days=order["production_days"]) > deadline:
                        violations.append(f"{a.supplier}: misses {o} production deadline")
                if logistics["delay_days"]:
                    risks.append(f"{supplier.name}: {logistics['delay_days']}-day port delay included in arrival check")
            except (ValueError, KeyError):
                violations.append(f"Invalid or unqualified allocation: {a.supplier}/{a.component}")
        for s, q in supplier_totals.items():
            if s not in self.nodes or self.nodes[s].type != "Supplier":
                violations.append(f"Unknown supplier {s}")
            elif not self.check_supplier_capacity(s, q)["feasible"]:
                violations.append(f"{self.nodes[s].name}: required {q:,} units/month; available {self.nodes[s].attributes['available_capacity']:,} units/month")
        for s, q in early_totals.items():
            if q > self.nodes[s].attributes.get("pre_tariff_capacity", 0):
                violations.append(f"{s}: pre-tariff dispatch capacity exceeded")
        for a in baseline.allocations:
            if component_totals[a.component] != a.quantity:
                violations.append(f"{a.component}: required {a.quantity:,}; allocated {component_totals[a.component]:,}")
        if set(component_totals) - {a.component for a in baseline.allocations}:
            violations.append("Plan includes components outside the incident scope")
        impact = self.get_downstream_impact()
        if residual_tariff:
            risks.append(f"€{money(residual_tariff):,.2f} residual tariff retained on incumbent MCU supply")
        risks.append("PMIC sourcing remains concentrated in one qualified supplier; approval authorizes a plan, not purchase execution")
        return PlanEvaluation(accepted=not violations, violations=list(dict.fromkeys(violations)),
            baseline_cost=money(baseline_cost), plan_cost=money(cost), incremental_cost=money(cost-pre_cost),
            savings=money(baseline_cost-cost), baseline_tariff=money(baseline_tariff), residual_tariff=money(residual_tariff),
            exposure_reduction=round((baseline_tariff-residual_tariff)/baseline_tariff*100, 2) if baseline_tariff else 0,
            orders_protected=len(impact["orders"]) if not violations else 0,
            order_value_protected=impact["order_value"] if not violations else 0, residual_risk=risks)

    def calculate_tariff_exposure(self):
        evaluation = self.simulate_sourcing_plan(self.baseline_plan())
        return dict(tariff= evaluation.baseline_tariff, baseline_cost=evaluation.baseline_cost,
                    components=[self.get_inventory(c) for c in self.get_downstream_impact()["components"]])
