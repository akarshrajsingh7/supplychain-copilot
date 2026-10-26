from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Entity(BaseModel):
    id: str
    name: str
    type: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class Relationship(BaseModel):
    id: str
    source: str
    target: str
    predicate: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class SupplyGraph(BaseModel):
    nodes: list[Entity]
    edges: list[Relationship]

    @model_validator(mode="after")
    def valid_references(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("Duplicate entity IDs")
        if any(e.source not in ids or e.target not in ids for e in self.edges):
            raise ValueError("Dangling relationship")
        if len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("Duplicate relationship IDs")
        return self


class FactKind(StrEnum):
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    DECISION = "DECISION"


class ProposedFact(BaseModel):
    subject: str
    predicate: str
    object: str
    source_episode: str
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=5)
    kind: FactKind
    valid_at: datetime
