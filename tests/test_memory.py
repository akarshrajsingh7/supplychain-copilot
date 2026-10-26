from datetime import datetime, timezone, timedelta
import pytest
from backend.graph.curator import GraphCurator
from backend.graph.graphiti_client import TemporalMemory


@pytest.mark.asyncio
async def test_temporal_curator(tmp_path):
    memory = TemporalMemory(path=tmp_path / "test.sqlite")
    curator = GraphCurator(memory, ["A"])
    t = datetime(2026, 10, 25, tzinfo=timezone.utc)
    await memory.add_episode("seed", {"source": "synthetic fixture"}, t)
    fact = dict(subject="A", predicate="EFFECTIVE_LANDED_COST", object="8.50", source_episode="seed",
                confidence=1, reason="Deterministic landed cost", kind="OBSERVATION", valid_at=t)
    await curator.commit_validated_fact(fact)
    assert (await curator.commit_validated_fact(fact))["status"] == "duplicate"
    await curator.commit_validated_fact({**fact, "object": "10.50", "valid_at": t+timedelta(days=1)})
    assert (await memory.search_graphiti_memory(subject="A"))[0]["object"] == "10.50"
    assert (await memory.search_graphiti_memory(subject="A", as_of=t.isoformat()))[0]["object"] == "8.50"
    with pytest.raises(ValueError, match="Confidence"):
        await curator.commit_validated_fact({**fact, "confidence": .5})
    with pytest.raises(ValueError, match="Missing source"):
        await curator.commit_validated_fact({**fact, "source_episode": "invented"})
    with pytest.raises(ValueError, match="human approval"):
        await curator.commit_validated_fact({**fact, "kind": "DECISION"})
