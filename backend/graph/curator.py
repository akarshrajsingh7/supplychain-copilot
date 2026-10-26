from backend.models.entities import FactKind, ProposedFact


class GraphCurator:
    def __init__(self, memory, entity_ids):
        self.memory = memory
        self.entity_ids = set(entity_ids)

    async def commit_validated_fact(self, proposed: dict, *, human_approved=False):
        fact = ProposedFact.model_validate(proposed)
        if fact.confidence < .9:
            raise ValueError("Confidence below 0.90")
        if fact.subject not in self.entity_ids:
            raise ValueError("Unknown subject")
        if not self.memory.has_episode(fact.source_episode):
            raise ValueError("Missing source episode")
        if fact.kind == FactKind.DECISION and not human_approved:
            raise ValueError("Decision requires human approval")
        if fact.predicate not in {"EFFECTIVE_LANDED_COST", "HAS_EXPOSURE", "CAPACITY_VERIFIED", "MITIGATION_DECISION"}:
            raise ValueError("Unapproved predicate")
        if fact.predicate == "MITIGATION_DECISION" and fact.kind != FactKind.DECISION:
            raise ValueError("Mitigation approval must be a decision")
        return await self.memory.commit(fact)
