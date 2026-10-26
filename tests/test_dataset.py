import json
from collections import Counter
from backend.config import DATA
from backend.models.entities import SupplyGraph
from backend.models.events import TariffEvent


def test_seed_integrity():
    graph = SupplyGraph.model_validate_json((DATA / "seed_supply_chain.json").read_text())
    counts = Counter(n.type for n in graph.nodes)
    assert counts["Supplier"] == 8
    assert counts["Component"] == 12
    assert counts["Product"] == 5
    assert counts["Order"] == 10
    assert counts["Country"] == 4
    assert len(graph.edges) > 100
    event = TariffEvent.model_validate_json((DATA / "tariff_event.json").read_text())
    assert event.reference_time < event.effective_at
    assert event.simulated
