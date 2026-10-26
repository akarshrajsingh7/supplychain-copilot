"""Deterministic fixture generator. Committed JSON is the runtime source of truth."""
import json
from pathlib import Path

nodes, edges = [], []


def node(id, name, type, **attributes):
    nodes.append(dict(id=id, name=name, type=type, attributes=attributes))


def edge(source, predicate, target, **attributes):
    edges.append(dict(id=f"{source}:{predicate}:{target}", source=source,
                      target=target, predicate=predicate, attributes=attributes))


for id, name in [("CN", "China"), ("TW", "Taiwan"), ("VN", "Vietnam"), ("DE", "Germany")]:
    node(id, name, "Country")
for code, name in [("8542", "Integrated circuits"), ("8532", "Capacitors"), ("8504", "Power supplies"), ("8473", "Electronics parts")]:
    node(code, f"HS {code}", "HSCode", category=name)
suppliers = [
    ("A", "Shenzhen Micro", "CN", 8000, 8000, 12, "shenzhen"),
    ("B", "Formosa Silicon", "TW", 6000, 2000, 14, "kaohsiung"),
    ("C", "VietCore Electronics", "VN", 5000, 1400, 10, "haiphong"),
    ("D", "Suzhou Logic", "CN", 7000, 5000, 12, "shenzhen"),
    ("E", "Rhein Power", "DE", 9000, 6000, 4, "hamburg"),
    ("F", "Hanoi Passives", "VN", 15000, 10000, 8, "haiphong"),
    ("G", "Taipei Precision", "TW", 8000, 5000, 9, "kaohsiung"),
    ("H", "Bavaria Assemblies", "DE", 12000, 9000, 3, "hamburg"),
]
for id, name, country, capacity, spare, lead, port in suppliers:
    node(id, name, "Supplier", country=country, monthly_capacity=capacity,
         available_capacity=spare, lead_days=lead, currency="EUR", qualified=True, pre_tariff_capacity=800 if id == "A" else 0,
         expedite_lead_days=4, booked_arrival="2026-11-17T00:00:00+00:00" if id in ["A", "D"] else None)
    node(f"cap-{id}", f"{spare:,} spare / month", "Capacity", available=spare, period="November 2026")
    edge(id, "LOCATED_IN", country)
    edge(id, "HAS_CAPACITY", f"cap-{id}")
    edge(id, "SHIPS_THROUGH", port)
for id, name, delay in [("shenzhen", "Shenzhen", 0), ("kaohsiung", "Kaohsiung", 3), ("haiphong", "Hai Phong", 1), ("hamburg", "Hamburg", 0)]:
    node(id, name, "Port", delay_days=delay, risk="congested" if delay else "normal")
for i, port in enumerate(["shenzhen", "kaohsiung", "haiphong"]):
    node(f"route-{i}", f"{port.title()} → Hamburg", "Route")
    edge(f"route-{i}", "CONNECTS", port)
    edge(f"route-{i}", "CONNECTS", "hamburg")
components = [
    ("mcu", "MCU · X7", "8542"), ("pmic", "Power IC · P4", "8542"),
    ("radio", "Radio module", "8473"), ("sensor", "Optical sensor", "8473"),
    ("cap", "Ceramic capacitor", "8532"), ("psu", "Power supply", "8504"),
    ("pcb", "Mainboard", "8473"), ("case", "Enclosure", "8473"),
    ("screen", "OLED display", "8473"), ("cable", "Cable harness", "8473"),
    ("fan", "Cooling assembly", "8473"), ("connector", "I/O connector", "8473"),
]
for id, name, hs in components:
    node(id, name, "Component", hs_code=hs, currency="EUR")
    edge(id, "HAS_HS_CODE", hs)
offers = [("A", "mcu", 8, .5), ("B", "mcu", 8.4, .6), ("C", "mcu", 8.8, .4),
          ("D", "pmic", 4, .3), ("G", "pmic", 4.5, .3)]
for s, c, price, freight in offers:
    edge(s, "SUPPLIES", c, unit_price=price, freight=freight, expedite_premium=.5, qualified=True)
for i, (c, _, _) in enumerate(components[2:]):
    edge(["E", "F", "G", "H"][i % 4], "SUPPLIES", c, unit_price=round(1.2 + i*.7, 2), freight=.15, qualified=True)
for i, name in enumerate(["Berlin Assembly", "Dresden Assembly"]):
    node(f"plant-{i}", name, "Plant", country="DE")
for i, name in enumerate(["Berlin Hub", "Hamburg Hub", "Dresden Hub"]):
    node(f"wh-{i}", name, "Warehouse", country="DE")
for c, quantity in [("mcu", 400), ("pmic", 200)]:
    edge("wh-0", "STOCKS", c, quantity=quantity)
    edge("wh-1", "IN_TRANSIT", c, quantity=200 if c == "mcu" else 100, arrival="2026-11-05")
for c, _, _ in components[2:]:
    edge("wh-2", "STOCKS", c, quantity=20000)
products = [("gateway", "Edge Gateway", .34), ("controller", "Smart Controller", .41),
            ("monitor", "Vision Monitor", .38), ("hub", "Connect Hub", .29), ("power", "Power Station", .26)]
for i, (id, name, margin) in enumerate(products):
    node(id, name, "Product", gross_margin=margin)
    edge(f"plant-{i%2}", "PRODUCES", id)
    for c in dict.fromkeys((["mcu", "pmic"] if i < 4 else []) + [components[2+i][0], "pcb", "case", "cable"]):
        edge(id, "REQUIRES", c, bom_quantity=1)
for i, name in enumerate(["Atlas Systems", "Northstar Labs", "Helix Industrial", "Nova Retail", "Orbit Health"]):
    node(f"customer-{i}", name, "Customer")
for i in range(10):
    p = products[i % 5][0]
    q = 575 if i%5 < 4 else 300  # 4,600 affected units; inventory 600 => procurement 4,000
    node(f"order-{i}", f"SO-{4100+i}", "Order", quantity=q,
         order_value=q*(120+i*5), deadline=f"2026-11-{25+i%3:02d}",
         gross_margin=products[i%5][2], production_days=3)
    edge(f"order-{i}", "CONTAINS", p, quantity=q)
    edge(f"order-{i}", "FOR_CUSTOMER", f"customer-{i%5}")

root = Path(__file__).resolve().parents[1] / "backend" / "data"
root.mkdir(exist_ok=True)
(root / "seed_supply_chain.json").write_text(json.dumps(dict(nodes=nodes, edges=edges), indent=2)+"\n")
(root / "tariff_event.json").write_text(json.dumps(dict(
    id="tariff-2026-11", name="Semiconductor tariff · 25%", simulated=True,
    country="CN", hs_code="8542", rate=.25,
    reference_time="2026-10-26T09:00:00Z", effective_at="2026-11-15T00:00:00Z",
    description="SIMULATED: A 25% tariff on HS 8542 integrated circuits imported from China takes effect November 15, 2026."
), indent=2)+"\n")
print(f"Generated {len(nodes)} entities and {len(edges)} relationships")
