"""Temporal journal plus real Graphiti persistence. Local mode is explicitly labelled.

The deterministic Graphiti path uses public EntityNode/EntityEdge/EpisodicNode.save,
so a failed probabilistic extractor can never destroy the source-of-truth evidence.
"""
import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from backend.config import RUNTIME
from backend.models.entities import ProposedFact, SupplyGraph
from backend.graph.embeddings import hash_embedding, LocalHashEmbedder

log = logging.getLogger(__name__)
GROUP = "supplychain-copilot"


def uid(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{GROUP}:{value}"))


class TemporalMemory:
    def __init__(self, mode="local", path=None):
        self.mode = mode
        self.path = path or RUNTIME / f"memory-{mode}.sqlite"
        self.client = None
        self.warning = None
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, body TEXT, reference_time TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS facts (id TEXT PRIMARY KEY, body TEXT, invalid_at TEXT)")

    def db(self):
        return sqlite3.connect(self.path)

    async def connect(self):
        if self.mode != "graphiti":
            return
        from graphiti_core import Graphiti
        from graphiti_core.llm_client import OpenAIClient, LLMConfig
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        # Structured node/edge saves and controlled reads need no remote model.
        # A placeholder key only constructs unused SDK clients; extraction requires a real key.
        key = os.getenv("OPENAI_API_KEY") or "unused-in-structured-mode"
        config = LLMConfig(api_key=key)
        self.client = Graphiti(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "supplychain-demo"),
            llm_client=OpenAIClient(config=config),
            embedder=LocalHashEmbedder(),
            cross_encoder=OpenAIRerankerClient(config=config))
        # Connected mode fails visibly if Neo4j is unavailable; never silently claims it is active.
        await self.client.driver.execute_query("RETURN 1 AS ready")
        await self.client.build_indices_and_constraints()

    async def close(self):
        if self.client:
            await self.client.close()

    async def load_supply_graph(self) -> SupplyGraph | None:
        if not self.client:
            return None
        records, _, _ = await self.client.driver.execute_query(
            "MATCH (n:Entity {group_id:$group}) WHERE n.entity_id IS NOT NULL "
            "RETURN n.entity_id AS id, n.name AS name, labels(n) AS types, n.properties_json AS properties",
            group=GROUP)
        nodes = [dict(id=r["id"], name=r["name"], type=next(t for t in r["types"] if t != "Entity"),
                      attributes=json.loads(r["properties"])) for r in records]
        records, _, _ = await self.client.driver.execute_query(
            "MATCH (s:Entity)-[r:RELATES_TO {group_id:$group}]->(t:Entity) "
            "WHERE s.entity_id IS NOT NULL AND t.entity_id IS NOT NULL AND r.properties_json IS NOT NULL "
            "RETURN s.entity_id AS source, t.entity_id AS target, r.name AS predicate, r.properties_json AS properties",
            group=GROUP)
        edges = [dict(id=f"{r['source']}:{r['predicate']}:{r['target']}", source=r["source"], target=r["target"],
                      predicate=r["predicate"], attributes=json.loads(r["properties"])) for r in records]
        return SupplyGraph(nodes=nodes, edges=edges)

    async def seed(self, graph: SupplyGraph):
        if not self.client:
            return
        from graphiti_core.nodes import EntityNode
        from graphiti_core.edges import EntityEdge
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        for n in graph.nodes:
            await EntityNode(uuid=uid(n.id), name=n.name, group_id=GROUP,
                labels=[n.type], summary=json.dumps(n.attributes),
                name_embedding=hash_embedding(n.name),
                attributes={"entity_id": n.id, "properties_json": json.dumps(n.attributes)}).save(self.client.driver)
        await self.add_episode("seed", {"kind": "OBSERVATION", "description": "Synthetic healthy supply chain", "entity_count": len(graph.nodes)}, now)
        for e in graph.edges:
            await EntityEdge(uuid=uid(e.id), group_id=GROUP, source_node_uuid=uid(e.source),
                target_node_uuid=uid(e.target), created_at=now, valid_at=now,
                name=e.predicate, fact=f"{e.source} {e.predicate} {e.target}: {json.dumps(e.attributes)}",
                fact_embedding=hash_embedding(f"{e.source} {e.predicate} {e.target}"),
                episodes=[uid("seed")], attributes={"properties_json": json.dumps(e.attributes)}).save(self.client.driver)

    async def add_episode(self, episode_id: str, body: dict, reference_time: datetime):
        body_json = json.dumps(body, default=str)
        if self.client:
            from graphiti_core.nodes import EpisodicNode, EpisodeType
            await EpisodicNode(uuid=uid(episode_id), name=episode_id, group_id=GROUP,
                source=EpisodeType.json, source_description="SIMULATED supply-chain evidence",
                content=body_json, valid_at=reference_time).save(self.client.driver)
            if os.getenv("GRAPHITI_EXTRACT", "false").lower() == "true":
                try:
                    # Extraction uses a separate episode, preserving the canonical structured one.
                    await asyncio.wait_for(self.client.add_episode(name=episode_id+"-extracted",
                        episode_body=body_json, source=EpisodeType.json,
                        source_description="Curated synthetic event", reference_time=reference_time,
                        group_id=GROUP+"-extraction", uuid=uid(episode_id+"-extracted")), timeout=25)
                except Exception as exc:
                    self.warning = "Extraction failed; structured Graphiti episode retained"
                    log.warning("%s: %s", self.warning, exc)
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO episodes VALUES (?, ?, ?)", (episode_id, body_json, reference_time.isoformat()))

    def has_episode(self, id: str):
        with self.db() as db:
            return db.execute("SELECT 1 FROM episodes WHERE id=?", (id,)).fetchone() is not None

    async def link_episode_entities(self, episode_id: str, entities: list[str]):
        """Provenance links for a trusted policy source, using Graphiti's MENTIONS edges."""
        if not self.client:
            return
        from graphiti_core.edges import EpisodicEdge
        for entity in entities:
            await EpisodicEdge(uuid=uid(episode_id+":mentions:"+entity), group_id=GROUP,
                source_node_uuid=uid(episode_id), target_node_uuid=uid(entity),
                created_at=datetime.now(timezone.utc)).save(self.client.driver)

    def has_fact(self, fact: ProposedFact):
        with self.db() as db:
            return db.execute("SELECT 1 FROM facts WHERE id=?", (self.fact_id(fact),)).fetchone() is not None

    async def latest_fact_time(self) -> datetime | None:
        if self.client:
            rows, _, _ = await self.client.driver.execute_query(
                "MATCH ()-[r:RELATES_TO {group_id:$group}]->() RETURN max(r.valid_at) AS latest", group=GROUP)
            latest = rows[0]["latest"] if rows else None
            return latest.to_native() if latest else None
        with self.db() as db:
            values = [datetime.fromisoformat(json.loads(r[0])["valid_at"])
                      for r in db.execute("SELECT body FROM facts").fetchall()]
        return max(values, default=None)

    @staticmethod
    def fact_id(fact: ProposedFact):
        return uid(fact.model_dump_json())

    async def commit(self, fact: ProposedFact):
        id = self.fact_id(fact)
        if self.has_fact(fact):
            return {"status": "duplicate", "id": id}
        if self.client:
            existing, _, _ = await self.client.driver.execute_query(
                "MATCH ()-[r:RELATES_TO {uuid:$id}]->() RETURN r.invalid_at AS invalid_at", id=id)
            if existing:
                invalid = existing[0]["invalid_at"]
                with self.db() as db:
                    db.execute("INSERT OR IGNORE INTO facts VALUES (?, ?, ?)",
                               (id, fact.model_dump_json(), str(invalid) if invalid else None))
                return {"status": "duplicate", "id": id, "backend": self.mode}
        with self.db() as db:
            rows = db.execute("SELECT id, body FROM facts WHERE invalid_at IS NULL").fetchall()
        superseded = []
        for old_id, raw in rows:
            old = json.loads(raw)
            if old["subject"] == fact.subject and old["predicate"] == fact.predicate:
                if datetime.fromisoformat(old["valid_at"]) > fact.valid_at:
                    raise ValueError("Out-of-order fact cannot supersede newer evidence")
                superseded.append(old_id)
        if self.client:
            from graphiti_core.nodes import EntityNode
            from graphiti_core.edges import EntityEdge, EpisodicEdge
            # Typed values may be literals. Their nodes retain their semantic role.
            await EntityNode(uuid=uid(fact.object), name=fact.object, group_id=GROUP,
                             labels=["EvidenceValue"], summary=fact.reason,
                             name_embedding=hash_embedding(fact.object)).save(self.client.driver)
            # Only supersede this predicate in this subject; history is never deleted.
            await self.client.driver.execute_query(
                "MATCH (s:Entity {uuid:$subject})-[r:RELATES_TO {name:$predicate}]->() "
                "WHERE r.invalid_at IS NULL AND r.group_id=$group "
                "SET r.invalid_at=$at, r.expired_at=$at",
                subject=uid(fact.subject), predicate=fact.predicate, group=GROUP, at=fact.valid_at)
            await EntityEdge(uuid=id, group_id=GROUP, source_node_uuid=uid(fact.subject),
                target_node_uuid=uid(fact.object), created_at=datetime.now(timezone.utc),
                name=fact.predicate, fact=fact.reason, valid_at=fact.valid_at,
                fact_embedding=hash_embedding(fact.reason),
                episodes=[uid(fact.source_episode)], attributes={"kind": fact.kind.value,
                    "confidence": fact.confidence, "source_episode": fact.source_episode}).save(self.client.driver)
            await EpisodicEdge(uuid=uid(id+"mention"), group_id=GROUP,
                source_node_uuid=uid(fact.source_episode), target_node_uuid=uid(fact.subject),
                created_at=datetime.now(timezone.utc)).save(self.client.driver)
        with self.db() as db:
            for old_id in superseded:
                db.execute("UPDATE facts SET invalid_at=? WHERE id=?", (fact.valid_at.isoformat(), old_id))
            db.execute("INSERT INTO facts VALUES (?, ?, NULL)", (id, fact.model_dump_json()))
        return {"status": "committed", "id": id, "backend": self.mode}

    async def search_graphiti_memory(self, query: str = "", subject: str | None = None, as_of: str | None = None):
        if self.client:
            # Controlled exact Graphiti query is deterministic and does not require embeddings.
            records, _, _ = await self.client.driver.execute_query(
                "MATCH (s:Entity)-[r:RELATES_TO]->(o:Entity) WHERE r.group_id=$group "
                "AND ($subject IS NULL OR s.uuid=$subject) "
                "AND ($at IS NULL AND r.invalid_at IS NULL OR $at IS NOT NULL "
                "AND r.valid_at <= datetime($at) AND (r.invalid_at IS NULL OR r.invalid_at > datetime($at))) "
                "AND (toLower(r.fact) CONTAINS toLower($query)) "
                "RETURN s.name AS subject, r.name AS predicate, o.name AS object, r.fact AS reason, "
                "r.valid_at AS valid_at, r.invalid_at AS invalid_at, r.episodes AS provenance, r.kind AS kind LIMIT 200",
                group=GROUP, subject=uid(subject) if subject else None, query=query, at=as_of)
            return [{k: str(v) if hasattr(v, "iso_format") else v for k, v in dict(r).items()} for r in records]
        with self.db() as db:
            rows = db.execute("SELECT body, invalid_at FROM facts").fetchall()
        facts = []
        for raw, invalid in rows:
            f = json.loads(raw)
            if subject and f["subject"] != subject:
                continue
            if as_of:
                at = datetime.fromisoformat(as_of)
                if datetime.fromisoformat(f["valid_at"]) > at or (invalid and datetime.fromisoformat(invalid) <= at):
                    continue
            elif invalid:
                continue
            if query.lower() in raw.lower():
                facts.append({**f, "invalid_at": invalid})
        return facts
