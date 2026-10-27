# LangSmith tracing and evaluations

The incident workflow supports both a scripted tool-calling model (`AGENT_MODE=deterministic`) and real OpenAI calls (`AGENT_MODE=live`). In live mode the Commander and six specialists use `ChatOpenAI` with [task-based model routing](MODEL_ROUTING.md): routine work uses a cheaper model, planning/critique use a thinking model, and sourcing escalates after rejection. Python tools still calculate costs, capacity, timing and policy limits. A human decision is required before recording approval.

## Current tracing-only setup

The configured key can ingest traces into the EU endpoint. It cannot read projects or write feedback, datasets or experiments. Keep these settings in the ignored `.env` file and restart the backend after changes:

```dotenv
AGENT_MODE=live
OPENAI_MODEL=gpt-4.1-mini
OPENAI_API_KEY=your_openai_key
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_tracing_key
LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com
LANGSMITH_PROJECT=supplychain-copilot
LANGSMITH_PUBLISH_FEEDBACK=false
```

`LANGSMITH_PROJECT` is the tracing project name, not the workspace name. Keys belong to a workspace and region. US-region keys use `https://api.smith.langchain.com`; use the endpoint matching your key.

1. Open the dashboard and confirm **LIVE COMMANDER**.
2. Inject a tariff event. Open **Run evidence** to inspect runtime, tool calls and checks.
3. Click **LangSmith** in the header, sign into the corresponding EU workspace and open the `supplychain-copilot` tracing project.
4. Find the investigation using the trace ID in **Run evidence**, or its `demo_run` metadata. Expand Commander, specialist task/model calls, tool spans and **Process evaluations**.
5. The evaluation span contains objective pass/pending/fail checks, metrics, spending enforcement and checkpoint evidence. These are trace outputs, not LangSmith feedback scores or an Experiment.
6. Approve or reject in the dashboard. The resumed decision has a separate trace ID, correlated by `demo_run`.

Tracing-only credentials cannot resolve direct trace links through project-read calls. By default the app opens the correct regional LangSmith homepage and displays trace IDs. Optional non-secret `LANGSMITH_WORKSPACE_ID` and `LANGSMITH_PROJECT_ID` UUIDs enable direct links without additional reads. Leave `LANGSMITH_RESOLVE_TRACE_URLS=false` with a tracing-only key.

Graph Q&A deliberately disables tracing around its provider calls. Keys entered in the Q&A form are transient and are not included in incident traces. The incident workflow sends synthetic company data and tool results to OpenAI and LangSmith when enabled.

## Repeatable local evaluations

```bash
uv run python -m backend.langsmith_evals
```

This runs three isolated cases: approve a feasible portfolio, recover a transient read and reject, and block a plan over the spending limit. Six graders check outcome, numerical accuracy, capacity rejection, replanning, the human boundary and process integrity. Results are saved under ignored `runtime/langsmith-evaluation-*.json`. Default execution is deterministic and disables cloud tracing/upload even if `.env` enables it. Each case has separate temporary memory and checkpoints; fixture decisions never affect the dashboard incident.

To evaluate the actual OpenAI workflow locally, explicitly run:

```bash
uv run python -m backend.langsmith_evals --mode live
```

This makes paid OpenAI calls but still disables LangSmith upload. Normal unit tests force deterministic mode and disable tracing to avoid using developer credentials.

## Optional feedback scores and Experiments

These features are implemented but remain disabled for the current tracing-only configuration. They require a PAT or service key with the relevant project, feedback, dataset and experiment permissions.

With such a key, `LANGSMITH_PUBLISH_FEEDBACK=true` publishes `process_*` scores after investigation and decision. The optional `POST /api/evaluation/publish` endpoint republishes current checks using stable feedback IDs. Failed observability writes do not change incident decisions.

An actual LangSmith Experiment can be created explicitly:

```bash
uv run python -m backend.langsmith_evals --upload
# Paid OpenAI variant:
uv run python -m backend.langsmith_evals --upload --mode live
```

The command checks dataset access before any model calls, prepares the versioned synthetic regression dataset and runs `langsmith.evaluation.aevaluate`. It does not run automatically from the dashboard. Cloud feedback/experiment publication has not been validated with the current tracing-only key.

Implementation: `backend/observability.py`, `backend/langsmith_evals.py`, `backend/engine.py`, `backend/agents/commander.py`. Safe status: `GET /api/observability`; current evidence: `GET /api/evaluation`.

References: [LangChain tracing](https://docs.langchain.com/langsmith/trace-with-langchain), [feedback](https://docs.langchain.com/langsmith/attach-user-feedback), [evaluation](https://docs.langchain.com/langsmith/evaluate-llm-application), [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

## Verified integration (2026-10-27)

A real `gpt-4.1-mini` isolated workflow exercised all six specialists, one transient read failure/retry, capacity rejection, verified replanning, durable human approval pause and rejection resume. All ten process checks passed; the LangSmith ingestion client reported no upload errors. The investigation trace ID is `01a17e8b-818f-7044-a444-4074bdaa1964` in the EU project. The tracing-only key cannot read the stored remote trace back; ingestion was verified, not remote UI retrieval. The rejection was a synthetic test fixture in temporary databases, not a dashboard decision. The dashboard remains ready for a fresh live incident.

Validation: 32 Python tests passed, one optional Neo4j test skipped; three local evaluation cases passed; frontend production build passed; a browser smoke check confirmed LIVE COMMANDER and the LangSmith evidence panel without JavaScript errors.
