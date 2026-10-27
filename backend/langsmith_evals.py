"""Isolated regression evaluations; optionally upload as a LangSmith experiment.

Default: deterministic models and no cloud upload. --mode live makes real OpenAI
calls. --upload requires dataset/experiment permissions, not a tracing-only key.
"""
import argparse
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import Client, tracing_context
from langsmith.evaluation import aevaluate
from langsmith.utils import LangSmithNotFoundError

from backend.config import RUNTIME
from backend.engine import IncidentEngine
from backend.evaluation import evaluate_run
from backend.graph.graphiti_client import TemporalMemory
from backend.models.runs import RunPolicy
from backend.observability import safe_error

DATASET_NAME = "supplychain-copilot-regression-v1"
GOLDEN_NUMBERS = {"savings": 7310, "baseline_tariff": 12300, "residual_tariff": 800, "orders_protected": 8}
CASES = [
    {"id": "approve-feasible-portfolio", "simulate_tool_failure": False, "max_incremental_cost_eur": 6000,
     "human_decision": "APPROVE", "expected_status": "APPROVED", "expected_decisions": 1},
    {"id": "recover-read-then-reject", "simulate_tool_failure": True, "max_incremental_cost_eur": 6000,
     "human_decision": "REJECT", "expected_status": "REJECTED", "expected_decisions": 0},
    {"id": "block-over-budget", "simulate_tool_failure": False, "max_incremental_cost_eur": 3000,
     "human_decision": None, "expected_status": "POLICY BLOCKED", "expected_decisions": 0},
]


async def evaluate_scenario(inputs, *, mode="deterministic"):
    # Every case gets a separate database, checkpointer and thread. Fixture human
    # decisions are synthetic test inputs, never approvals of the dashboard run.
    with tempfile.TemporaryDirectory(prefix="supplychain-eval-") as directory:
        path = Path(directory)
        memory = TemporalMemory("local", path=path/"memory.sqlite")
        async with AsyncSqliteSaver.from_conn_string(str(path/"checkpoints.sqlite")) as saver:
            await saver.setup()
            engine = IncidentEngine(memory, saver, state_path=path/"state.json", mode=mode,
                                    step_delay=0, auto_feedback=False)
            await engine.initialize()
            await engine.inject(RunPolicy(simulate_tool_failure=inputs["simulate_tool_failure"],
                max_incremental_cost_eur=inputs["max_incremental_cost_eur"]))
            await engine.run()
            if engine.state["status"] == "AWAITING HUMAN APPROVAL" and inputs.get("human_decision"):
                await engine.decide(inputs["human_decision"])
            report = await evaluate_run(engine)
            return {"status": engine.state["status"], "error": engine.state["error"],
                "checks": {c["id"]: c["status"] for c in report["checks"]},
                "numbers": report["evaluation"] or {}, "metrics": report["metrics"],
                "decision_count": len([f for f in report["provenance"] if f["kind"] == "DECISION"]),
                "checkpoint": report["checkpoint"], "synthetic_test": True, "agent_mode": mode}


def expected_outcome(outputs, reference_outputs):
    return {"key": "expected_outcome", "score": int(outputs["status"] == reference_outputs["expected_status"])}


def numeric_accuracy(outputs, reference_outputs):
    return {"key": "numeric_accuracy", "score": int(all(outputs["numbers"].get(k) == v
        for k, v in reference_outputs["numbers"].items()))}


def capacity_rejection(outputs):
    return {"key": "capacity_rejection", "score": int(outputs["checks"].get("capacity_rejection") == "pass")}


def verified_replanning(outputs):
    return {"key": "verified_replanning", "score": int(all(outputs["checks"].get(k) == "pass"
        for k in ["replan", "verified_portfolio"]))}


def human_boundary(outputs, reference_outputs):
    return {"key": "human_boundary", "score": int(outputs["decision_count"] == reference_outputs["expected_decisions"]
        and outputs["checks"].get("decision_gate") == "pass")}


def process_integrity(outputs):
    return {"key": "process_integrity", "score": int(bool(outputs["checks"]) and
        all(value != "fail" for value in outputs["checks"].values()) and outputs["status"] != "ERROR")}


EVALUATORS = [expected_outcome, numeric_accuracy, capacity_rejection, verified_replanning, human_boundary, process_integrity]


def reference(case):
    return {"expected_status": case["expected_status"], "expected_decisions": case["expected_decisions"], "numbers": GOLDEN_NUMBERS}


async def run_local(mode):
    rows = []
    with tracing_context(enabled=False):
        for case in CASES:
            output = await evaluate_scenario(case, mode=mode)
            expected = reference(case)
            scores = [expected_outcome(output, expected), numeric_accuracy(output, expected), capacity_rejection(output),
                      verified_replanning(output), human_boundary(output, expected), process_integrity(output)]
            rows.append({"case": case["id"], "outputs": output, "scores": scores})
    return {"mode": mode, "cloud_upload": False, "cases": rows,
            "passed_cases": sum(all(s["score"] == 1 for s in row["scores"]) for row in rows), "total_cases": len(rows)}


def prepare_dataset(client):
    # Permission check occurs before any model calls or dataset mutation.
    list(client.list_datasets(limit=1))
    try:
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
    except LangSmithNotFoundError:
        dataset = client.create_dataset(DATASET_NAME, description="Synthetic tariff harness regression cases; fixture approvals only.")
    ids = {str(example.id) for example in client.list_examples(dataset_id=dataset.id)}
    for case in CASES:
        id = uuid5(NAMESPACE_URL, f"{dataset.id}:{case['id']}")
        if str(id) not in ids:
            client.create_example(inputs=case, reference_outputs=reference(case), dataset_id=dataset.id, example_id=id)
    return dataset


async def run_uploaded(mode, *, client=None):
    client = client or Client(timeout_ms=15000)
    dataset = await asyncio.to_thread(prepare_dataset, client)

    async def target(inputs):
        return await evaluate_scenario(inputs, mode=mode)

    results = await aevaluate(target, data=DATASET_NAME, evaluators=EVALUATORS, client=client,
        experiment_prefix="supplychain-"+mode, max_concurrency=1,
        metadata={"synthetic": True, "agent_mode": mode, "fixture_version": "v1-25pct-china", "human_decisions": "simulated-test-fixtures"})
    rows = []
    async for row in results:
        rows.append({"scores": [{"key": score.key, "score": score.score}
                                for score in row["evaluation_results"]["results"]]})
    return {"mode": mode, "cloud_upload": True, "experiment_name": results.experiment_name,
            "experiment_id": str(results.experiment_id), "dataset_url": dataset.url, "cases": rows}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["deterministic", "live"], default="deterministic")
    parser.add_argument("--upload", action="store_true", help="Upload an actual LangSmith Dataset/Experiment; needs a PAT/service key")
    args = parser.parse_args()
    try:
        result = await run_uploaded(args.mode) if args.upload else await run_local(args.mode)
    except Exception as error:
        print(safe_error(error))
        raise SystemExit(2) from None
    path = RUNTIME / ("langsmith-evaluation-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".json")
    path.write_text(json.dumps(result, indent=2, default=str))
    print("Evaluation report:", path)
    if args.upload:
        print("LangSmith experiment:", result["experiment_name"])
        print("Dataset:", result["dataset_url"])
    else:
        print(f"{result['passed_cases']}/{result['total_cases']} scenarios passed; no LangSmith cloud upload.")


if __name__ == "__main__":
    asyncio.run(main())
